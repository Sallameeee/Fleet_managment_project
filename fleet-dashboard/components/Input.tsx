import type { InputHTMLAttributes } from "react";

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
};

export default function Input({ label, id, className = "", ...props }: InputProps) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-slate-300">{label}</span>
      <input
        id={id}
        className={
          "w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 " +
          "placeholder:text-slate-500 focus:border-brand focus:outline-none " +
          "focus:ring-2 focus:ring-brand/40 " +
          className
        }
        {...props}
      />
    </label>
  );
}
