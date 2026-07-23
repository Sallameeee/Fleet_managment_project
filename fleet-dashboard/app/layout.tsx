import type { Metadata } from "next";
import "./globals.css";
import { LangProvider } from "@/lib/i18n";
import { ToastProvider } from "@/lib/toast";

export const metadata: Metadata = {
  title: "Fleet Admin",
  description: "Super admin panel for the bus-tracking platform",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // The inline script below sets lang/dir/theme on <html> BEFORE React
    // hydrates (from localStorage), so the server's `lang="en"` + no-theme-class
    // deliberately differ from the hydrated DOM. suppressHydrationWarning is the
    // documented Next.js pattern for this — it applies only to THIS element's own
    // attributes/class (one level deep), never its children.
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Apply the saved language + theme before paint, so there is no flash of
            the wrong direction/theme and the attributes are correct pre-hydration. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{var e=document.documentElement;if(localStorage.getItem('fleet_theme')==='light')e.classList.add('light');var l=localStorage.getItem('fleet_lang');if(l==='ar'){e.lang='ar';e.dir='rtl';}}catch(e){}",
          }}
        />
      </head>
      <body className="min-h-screen bg-ink-950 text-slate-100 antialiased">
        <LangProvider>
          <ToastProvider>{children}</ToastProvider>
        </LangProvider>
      </body>
    </html>
  );
}
