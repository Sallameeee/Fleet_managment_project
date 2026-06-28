import type { Metadata } from "next";
import "./globals.css";
import { LangProvider } from "@/lib/i18n";

export const metadata: Metadata = {
  title: "Fleet Admin",
  description: "Super admin panel for the bus-tracking platform",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        {/* Apply the saved theme before paint to avoid a flash. Default = dark. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{var e=document.documentElement;if(localStorage.getItem('fleet_theme')==='light')e.classList.add('light');var l=localStorage.getItem('fleet_lang');if(l==='ar'){e.lang='ar';e.dir='rtl';}}catch(e){}",
          }}
        />
      </head>
      <body className="min-h-screen bg-ink-950 text-slate-100 antialiased">
        <LangProvider>{children}</LangProvider>
      </body>
    </html>
  );
}
