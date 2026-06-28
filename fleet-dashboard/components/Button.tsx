import type { ButtonHTMLAttributes } from "react";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  loading?: boolean;
};

export default function Button({
  loading = false,
  disabled,
  children,
  className = "",
  ...props
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={
        "w-full rounded-lg bg-brand px-4 py-2.5 font-medium text-white " +
        "transition-colors hover:bg-brand-sage focus:outline-none focus:ring-2 " +
        "focus:ring-brand-sage/60 disabled:cursor-not-allowed disabled:opacity-60 " +
        className
      }
      {...props}
    >
      {loading ? "Please wait…" : children}
    </button>
  );
}
