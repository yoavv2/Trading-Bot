import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { KillSwitchBanner } from "@/components/KillSwitchBanner";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Operator Console",
  description: "Read-only operator console for the trading platform.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full flex flex-col bg-zinc-950 text-zinc-100">
        <nav className="flex items-center gap-6 border-b border-zinc-800 px-4 py-2 text-sm">
          <span className="font-semibold tracking-tight text-zinc-300">
            Operator Console
          </span>
          <Link href="/" className="text-zinc-400 hover:text-zinc-100">
            System Status
          </Link>
          <Link href="/strategy" className="text-zinc-400 hover:text-zinc-100">
            Strategy
          </Link>
          <Link href="/runs" className="text-zinc-400 hover:text-zinc-100">
            Runs
          </Link>
        </nav>
        <KillSwitchBanner />
        {children}
      </body>
    </html>
  );
}
