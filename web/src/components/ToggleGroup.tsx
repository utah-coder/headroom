// Segmented toggle (Requirement 18.8, design "Components").
// A labelled group of native buttons: Tab reaches each one, Enter and Space
// activate it natively, aria-pressed exposes the selected option, and
// focus-visible draws a ring for keyboard users.

export interface ToggleOption<T extends string> {
  value: T;
  label: string;
}

interface ToggleGroupProps<T extends string> {
  /** Accessible name of the group, also shown as a small visible caption. */
  label: string;
  options: readonly ToggleOption<T>[];
  value: T;
  onChange: (value: T) => void;
}

export default function ToggleGroup<T extends string>({
  label,
  options,
  value,
  onChange,
}: ToggleGroupProps<T>) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-slate-300">{label}</span>
      <div
        role="group"
        aria-label={label}
        className="inline-flex rounded-lg border border-slate-700 bg-slate-900 p-1"
      >
        {options.map((opt) => {
          const pressed = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              aria-pressed={pressed}
              onClick={() => onChange(opt.value)}
              className={
                'rounded-md px-3 py-1.5 text-sm font-medium transition-colors ' +
                'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300 ' +
                (pressed
                  ? 'bg-slate-100 text-slate-950'
                  : 'text-slate-300 hover:bg-slate-800 hover:text-slate-100')
              }
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
