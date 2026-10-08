import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { HOME_HREF, PRIMARY_NAVIGATION, PRODUCT_NAME } from "@/lib/navigation";
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
  title: "Strategy Research",
  description:
    "Research console: author strategy specifications, pick assets, run studies and compare evidence.",
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
        <nav
          aria-label="Primary"
          className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-zinc-800 px-4 py-2 text-sm"
        >
          <Link
            href={HOME_HREF}
            className="font-semibold tracking-tight text-cyan-200 hover:text-cyan-100"
          >
            {PRODUCT_NAME}
          </Link>
          {PRIMARY_NAVIGATION.map((item) => (
            <Link key={item.href} href={item.href} className="text-zinc-400 hover:text-zinc-100">
              {item.label}
            </Link>
          ))}
        </nav>
        {children}
      </body>
    </html>
  );
}
