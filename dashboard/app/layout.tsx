import "./globals.css";
import { ReactNode } from "react";

export const metadata = {
  title: "Polymarket Bot Dashboard",
  description: "Live monitoring for the Polymarket tweet bot",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-950 text-slate-100">
        {children}
      </body>
    </html>
  );
}
