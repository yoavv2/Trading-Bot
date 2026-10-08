"""Catalog reads and saved asset lists for the research console (proposal Part G).

Search covers the local catalog only and names are present only where a public
directory or a viewed asset's metadata supplied one; ``name_coverage`` gives the
numbers the UI must show next to every name search. Saved lists are named, reusable
and editable; a study snapshots a list's tickers at revision creation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trading_platform.core.settings import Settings
from trading_platform.db.models.research import AssetCatalogEntry, AssetList, AssetListItem
from trading_platform.db.session import session_scope
from trading_platform.services.research.catalog import (
    PROVIDER,
    get_asset,
    name_coverage,
    normalize_ticker,
    search_assets,
)

NAME_SEARCH_NOTE = (
    "Name search covers populated names only: names come from the public Nasdaq Trader and SEC "
    "directories at catalog sync and from the provider's metadata when an asset is viewed. "
    "Unnamed assets are found by ticker only."
)
COVERAGE_NOTE = (
    "Catalog dates say what the provider claims to hold. Usable history for a study is checked "
    "per strategy and asset by readiness (warm-up before the development window, range end), "
    "and by the integrity check after download."
)
MAX_LIST_NAME = 120
MAX_LIST_ITEMS = 500


class AssetListError(Exception):
    code = "asset_list_error"
    status = 422


class AssetListNotFoundError(AssetListError):
    code = "asset_list_not_found"
    status = 404

    def __init__(self, list_id: uuid.UUID) -> None:
        self.list_id = list_id
        super().__init__(f"asset list {list_id} not found")


class AssetListNameTakenError(AssetListError):
    code = "asset_list_name_taken"
    status = 409


class InvalidAssetListError(AssetListError):
    code = "invalid_asset_list"

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def entry_to_dict(entry: AssetCatalogEntry) -> dict[str, Any]:
    return {
        "ticker": entry.ticker,
        "name": entry.name,
        "name_source": entry.name_source,
        "exchange": entry.exchange,
        "asset_type": entry.asset_type,
        "currency": entry.currency,
        "catalog_start": entry.catalog_start.isoformat() if entry.catalog_start else None,
        "catalog_end": entry.catalog_end.isoformat() if entry.catalog_end else None,
        "names_fetched_at": entry.names_fetched_at.isoformat() if entry.names_fetched_at else None,
        "synced_at": entry.synced_at.isoformat() if entry.synced_at else None,
    }


def list_to_dict(asset_list: AssetList, items: list[AssetListItem]) -> dict[str, Any]:
    return {
        "list_id": str(asset_list.id),
        "name": asset_list.name,
        "tickers": [item.ticker for item in sorted(items, key=lambda i: (i.position, i.ticker))],
        "created_at": asset_list.created_at.isoformat() if asset_list.created_at else None,
        "updated_at": asset_list.updated_at.isoformat() if asset_list.updated_at else None,
    }


def _check_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_LIST_NAME:
        raise InvalidAssetListError("name", f"must be 1..{MAX_LIST_NAME} characters")
    return name.strip()


def _check_tickers(tickers: Any) -> list[str]:
    if not isinstance(tickers, list):
        raise InvalidAssetListError("tickers", "must be a list")
    out: list[str] = []
    for raw in tickers:
        if not isinstance(raw, str) or not raw.strip():
            raise InvalidAssetListError("tickers", "must be nonblank strings")
        ticker = normalize_ticker(raw)
        if ticker not in out:
            out.append(ticker)
    if len(out) > MAX_LIST_ITEMS:
        raise InvalidAssetListError("tickers", f"at most {MAX_LIST_ITEMS} tickers")
    return out


class AssetCatalogService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def coverage(self) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            named, total = name_coverage(session)
            last = session.execute(select(AssetCatalogEntry.synced_at).order_by(AssetCatalogEntry.synced_at.desc()).limit(1)).scalar_one_or_none()
            return {
                "provider": PROVIDER,
                "rows_total": total,
                "rows_named": named,
                "name_search_note": NAME_SEARCH_NOTE,
                "coverage_note": COVERAGE_NOTE,
                "last_synced_at": last.isoformat() if last else None,
                "max_assets_per_study": self._settings.research.max_assets_per_study,
            }

    def search(self, query: str, *, limit: int = 20) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            rows = search_assets(session, query, limit=limit)
            named, total = name_coverage(session)
            return {
                "query": query,
                "count": len(rows),
                "items": [entry_to_dict(r) for r in rows],
                "name_coverage": {"rows_named": named, "rows_total": total},
                "name_search_note": NAME_SEARCH_NOTE,
            }

    def asset(self, ticker: str) -> dict[str, Any] | None:
        with session_scope(self._settings) as session:
            entry = get_asset(session, ticker)
            return {**entry_to_dict(entry), "coverage_note": COVERAGE_NOTE} if entry else None

    # -- saved lists ---------------------------------------------------------

    def list_lists(self) -> list[dict[str, Any]]:
        with session_scope(self._settings) as session:
            lists = list(session.execute(select(AssetList).order_by(AssetList.name)).scalars())
            out = []
            for asset_list in lists:
                items = list(session.execute(select(AssetListItem).where(AssetListItem.list_id == asset_list.id)).scalars())
                out.append(list_to_dict(asset_list, items))
            return out

    def get_list(self, list_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            return list_to_dict(*self._load(session, list_id))

    def create_list(self, *, name: str, tickers: list[str]) -> dict[str, Any]:
        checked_name, checked = _check_name(name), _check_tickers(tickers)
        with session_scope(self._settings) as session:
            asset_list = AssetList(id=uuid.uuid4(), name=checked_name)
            session.add(asset_list)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AssetListNameTakenError(checked_name) from exc
            items = self._replace_items(session, asset_list.id, checked)
            session.refresh(asset_list)
            return list_to_dict(asset_list, items)

    def update_list(self, list_id: uuid.UUID, *, name: str | None = None, tickers: list[str] | None = None) -> dict[str, Any]:
        checked_name = _check_name(name) if name is not None else None
        checked = _check_tickers(tickers) if tickers is not None else None
        with session_scope(self._settings) as session:
            asset_list, items = self._load(session, list_id)
            if checked_name is not None:
                asset_list.name = checked_name
            asset_list.updated_at = datetime.now(UTC)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AssetListNameTakenError(checked_name or "") from exc
            if checked is not None:
                items = self._replace_items(session, list_id, checked)
            session.refresh(asset_list)
            return list_to_dict(asset_list, items)

    def delete_list(self, list_id: uuid.UUID) -> dict[str, Any]:
        with session_scope(self._settings) as session:
            asset_list, _items = self._load(session, list_id)
            session.delete(asset_list)
            session.flush()
            return {"list_id": str(list_id), "deleted": True}

    @staticmethod
    def _load(session: Session, list_id: uuid.UUID) -> tuple[AssetList, list[AssetListItem]]:
        asset_list = session.get(AssetList, list_id)
        if asset_list is None:
            raise AssetListNotFoundError(list_id)
        items = list(session.execute(select(AssetListItem).where(AssetListItem.list_id == list_id)).scalars())
        return asset_list, items

    @staticmethod
    def _replace_items(session: Session, list_id: uuid.UUID, tickers: list[str]) -> list[AssetListItem]:
        for existing in session.execute(select(AssetListItem).where(AssetListItem.list_id == list_id)).scalars():
            session.delete(existing)
        session.flush()
        items = [AssetListItem(id=uuid.uuid4(), list_id=list_id, ticker=t, position=i) for i, t in enumerate(tickers)]
        session.add_all(items)
        session.flush()
        return items
