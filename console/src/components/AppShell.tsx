"use client";

import { useEffect, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { HOME_HREF, PRIMARY_NAVIGATION, PRODUCT_NAME } from "@/lib/navigation";

function subscribeTheme(callback: () => void) {
  window.addEventListener("research-theme", callback);
  return () => window.removeEventListener("research-theme", callback);
}
function themeSnapshot() { return document.documentElement.dataset.theme ?? "light"; }
function toggleTheme() {
  const next = themeSnapshot() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("research-theme", next); } catch { /* Storage may be unavailable. */ }
  window.dispatchEvent(new Event("research-theme"));
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  useEffect(() => {
    try { document.documentElement.dataset.theme = localStorage.getItem("research-theme") === "dark" ? "dark" : "light"; } catch { /* Use the light default. */ }
    window.dispatchEvent(new Event("research-theme"));
  }, []);
  const theme = useSyncExternalStore(subscribeTheme, themeSnapshot, () => "light");
  const area = pathname.startsWith("/jobs") ? "System activity" : pathname.includes("/strategies") ? "Strategies" : pathname.includes("/assets") ? "Asset lists" : "Research dashboard";
  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">Skip to content</a>
      <aside className="app-sidebar">
        <Link href={HOME_HREF} className="app-brand" aria-label={PRODUCT_NAME}>
          <span className="brand-symbol" aria-hidden="true">S</span>
          <span>Strategy<span className="brand-subtitle">Research</span></span>
        </Link>
        <p className="sidebar-label">WORKSPACE</p>
        <nav aria-label="Primary" className="sidebar-nav">
          {PRIMARY_NAVIGATION.map((item, index) => (
            <Link key={item.href} href={item.href} aria-current={pathname.startsWith(item.href) ? "page" : undefined}>
              <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
                {index === 0 ? <path d="M3 3h5v6H3zM12 3h5v3h-5zM3 13h5v4H3zM12 10h5v7h-5z" /> : index === 1 ? <path d="M3 16V9m7 7V4m7 12V7M2 17h16" /> : <path d="M3 4h14v12H3zM3 8h14M8 8v8" />}
              </svg>
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <Link href="/jobs" aria-current={pathname.startsWith("/jobs") ? "page" : undefined} className="system-link">System activity <span aria-hidden="true">↗</span></Link>
          <p>Build ideas.<br />Test with evidence.</p>
        </div>
      </aside>
      <div className="app-workspace">
        <header className="app-topbar">
          <div><span className="muted">Workspace</span><span className="breadcrumb-divider">/</span>{area}</div>
          <button type="button" className="theme-toggle" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}><span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span> {theme === "dark" ? "Light" : "Dark"}</button>
        </header>
        <div id="main-content" tabIndex={-1}>{children}</div>
      </div>
    </div>
  );
}
