import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "RAY — Merchant Financial Revenue Recovery Control Plane",
  description: "Autonomous payment recovery engine with deterministic policy governance and cryptographic audit receipts.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark h-full">
      <body className={`${inter.variable} ${inter.className} min-h-full bg-[#080b11] text-slate-100 antialiased selection:bg-emerald-500/25 selection:text-emerald-200`}>
        {children}
      </body>
    </html>
  );
}
