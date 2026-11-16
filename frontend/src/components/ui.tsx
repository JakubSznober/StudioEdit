// Podstawowe komponenty UI (przyciski, karty, paski postępu, przełączniki, dialogi, zakładki…)
import { AnimatePresence, motion } from 'framer-motion';
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { create } from 'zustand';
import { cx } from '../lib/format';
import { LoaderCircle, X, type LucideIcon } from '../lib/icons';

// ------------------------------------------------------------------ Button
type BtnVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline' | 'soft';
type BtnSize = 'xs' | 'sm' | 'md' | 'lg';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant;
  size?: BtnSize;
  icon?: LucideIcon;
  iconRight?: LucideIcon;
  loading?: boolean;
}

const BTN_V: Record<BtnVariant, string> = {
  primary:
    'accent-gradient text-white shadow-[0_6px_20px_-6px_color-mix(in_oklab,var(--c-accent)_70%,transparent)] hover:brightness-110 active:brightness-95',
  secondary: 'bg-surface text-text border border-border shadow-soft hover:bg-surface-2 hover:border-border-strong',
  outline: 'bg-transparent text-text border border-border hover:bg-surface-2',
  ghost: 'bg-transparent text-muted hover:text-text hover:bg-surface-2',
  soft: 'bg-accent/10 text-accent hover:bg-accent/15',
  danger: 'bg-danger/10 text-danger border border-danger/20 hover:bg-danger/15',
};
const BTN_S: Record<BtnSize, string> = {
  xs: 'h-6 px-2 text-[11.5px] gap-1 rounded-md',
  sm: 'h-7.5 px-2.5 text-[12.5px] gap-1.5 rounded-lg',
  md: 'h-9 px-3.5 text-[13px] gap-2 rounded-[10px]',
  lg: 'h-11 px-5 text-[14px] gap-2 rounded-xl',
};
const ICON_S: Record<BtnSize, number> = { xs: 12, sm: 14, md: 15, lg: 17 };

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon: Icon, iconRight: IconR, loading, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(
        'inline-flex items-center justify-center font-medium whitespace-nowrap select-none transition-all duration-150 outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:opacity-50 disabled:pointer-events-none cursor-pointer',
        BTN_V[variant],
        BTN_S[size],
        className,
      )}
      {...rest}
    >
      {loading ? <LoaderCircle size={ICON_S[size]} className="animate-spin" /> : Icon ? <Icon size={ICON_S[size]} /> : null}
      {children}
      {IconR && !loading ? <IconR size={ICON_S[size]} /> : null}
    </button>
  );
});

export function IconButton({
  icon: Icon,
  label,
  active,
  size = 'md',
  className,
  ...rest
}: { icon: LucideIcon; label: string; active?: boolean; size?: 'sm' | 'md' | 'lg' } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const dim = size === 'sm' ? 'h-7 w-7' : size === 'lg' ? 'h-10 w-10' : 'h-8 w-8';
  const is = size === 'sm' ? 14 : size === 'lg' ? 18 : 16;
  return (
    <Tip text={label}>
      <button
        aria-label={label}
        className={cx(
          'inline-flex items-center justify-center rounded-lg transition-all duration-150 cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:opacity-40 disabled:pointer-events-none',
          dim,
          active ? 'bg-accent/12 text-accent' : 'text-muted hover:text-text hover:bg-surface-2',
          className,
        )}
        {...rest}
      >
        <Icon size={is} />
      </button>
    </Tip>
  );
}

// ------------------------------------------------------------------ Card
export function Card({
  className,
  children,
  hover,
  delay = 0,
  ...rest
}: { hover?: boolean; delay?: number } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay, ease: [0.22, 1, 0.36, 1] }}
      className={cx(
        'rounded-2xl border border-border bg-surface shadow-soft',
        hover && 'transition-shadow duration-200 hover:shadow-card hover:border-border-strong',
        className,
      )}
      {...(rest as Record<string, unknown>)}
    >
      {children}
    </motion.div>
  );
}

export function CardHeader({
  title,
  subtitle,
  icon: Icon,
  iconColor,
  right,
  className,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: LucideIcon;
  iconColor?: string;
  right?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cx('flex items-start gap-3 px-5 pt-4 pb-3', className)}>
      {Icon && (
        <div
          className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px]"
          style={{ background: `color-mix(in oklab, ${iconColor || 'var(--c-accent)'} 14%, transparent)`, color: iconColor || 'var(--c-accent)' }}
        >
          <Icon size={16} />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="text-[14px] font-semibold tracking-[-0.01em] text-text">{title}</div>
        {subtitle && <div className="mt-0.5 text-[12.5px] text-muted">{subtitle}</div>}
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ Badge / chip
export type Tone = 'neutral' | 'accent' | 'success' | 'warn' | 'danger' | 'info';
const TONE: Record<Tone, string> = {
  neutral: 'bg-surface-3 text-muted',
  accent: 'bg-accent/12 text-accent',
  success: 'bg-success/12 text-success',
  warn: 'bg-warn/14 text-warn',
  danger: 'bg-danger/12 text-danger',
  info: 'bg-info/12 text-info',
};

export function Badge({
  tone = 'neutral',
  children,
  className,
  dot,
  color,
  title,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
  dot?: boolean;
  color?: string;
  title?: string;
}) {
  const style = color ? { background: `color-mix(in oklab, ${color} 14%, transparent)`, color } : undefined;
  return (
    <span
      title={title}
      style={style}
      className={cx(
        'inline-flex items-center gap-1 rounded-md px-1.5 py-[1px] text-[11px] font-medium leading-[18px] whitespace-nowrap',
        !color && TONE[tone],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export function StatusDot({ tone, pulse, className }: { tone: Tone; pulse?: boolean; className?: string }) {
  const c: Record<Tone, string> = {
    neutral: 'bg-subtle',
    accent: 'bg-accent',
    success: 'bg-success',
    warn: 'bg-warn',
    danger: 'bg-danger',
    info: 'bg-info',
  };
  return (
    <span className={cx('relative inline-flex h-2 w-2', className)}>
      {pulse && <span className={cx('absolute inline-flex h-full w-full animate-ping rounded-full opacity-60', c[tone])} />}
      <span className={cx('relative inline-flex h-2 w-2 rounded-full', c[tone])} />
    </span>
  );
}

// ------------------------------------------------------------------ Progress
export function Progress({
  value,
  tone = 'accent',
  className,
  striped,
  color,
  height = 6,
  indeterminate,
}: {
  value: number;
  tone?: Tone;
  className?: string;
  striped?: boolean;
  color?: string;
  height?: number;
  indeterminate?: boolean;
}) {
  const v = Math.max(0, Math.min(1, value || 0));
  const bg: Record<Tone, string> = {
    neutral: 'var(--c-subtle)',
    accent: 'linear-gradient(90deg, var(--c-accent), var(--c-accent-2))',
    success: 'var(--c-success)',
    warn: 'var(--c-warn)',
    danger: 'var(--c-danger)',
    info: 'var(--c-info)',
  };
  return (
    <div className={cx('relative w-full overflow-hidden rounded-full bg-surface-3', className)} style={{ height }}>
      {indeterminate ? (
        <motion.div
          className="absolute inset-y-0 w-1/3 rounded-full"
          style={{ background: color || bg[tone] }}
          animate={{ left: ['-35%', '100%'] }}
          transition={{ duration: 1.3, repeat: Infinity, ease: 'easeInOut' }}
        />
      ) : (
        <motion.div
          className={cx('h-full rounded-full', striped && 'progress-stripes')}
          style={{ background: color || bg[tone] }}
          initial={false}
          animate={{ width: `${v * 100}%` }}
          transition={{ type: 'spring', stiffness: 120, damping: 22 }}
        />
      )}
    </div>
  );
}

export function Ring({ value, size = 56, stroke = 6, color, children }: { value: number; size?: number; stroke?: number; color?: string; children?: React.ReactNode }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value || 0));
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--c-surface-3)" strokeWidth={stroke} fill="none" />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={color || 'var(--c-accent)'}
          strokeWidth={stroke}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={c}
          initial={false}
          animate={{ strokeDashoffset: c * (1 - v) }}
          transition={{ type: 'spring', stiffness: 80, damping: 20 }}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">{children}</div>
    </div>
  );
}

// ------------------------------------------------------------------ formularze
export function Switch({
  checked,
  onChange,
  disabled,
  size = 'md',
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  size?: 'sm' | 'md';
  label?: string;
}) {
  const w = size === 'sm' ? 28 : 36;
  const h = size === 'sm' ? 16 : 20;
  const k = h - 4;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        'relative inline-flex shrink-0 items-center rounded-full transition-colors duration-200 cursor-pointer disabled:opacity-50 outline-none focus-visible:ring-2 focus-visible:ring-accent/40',
        checked ? 'bg-accent' : 'bg-border-strong',
      )}
      style={{ width: w, height: h }}
    >
      <motion.span
        className="absolute rounded-full bg-white shadow"
        style={{ width: k, height: k, top: 2 }}
        initial={false}
        animate={{ left: checked ? w - k - 2 : 2 }}
        transition={{ type: 'spring', stiffness: 500, damping: 32 }}
      />
    </button>
  );
}

const fieldCls =
  'w-full rounded-[10px] border border-border bg-surface px-3 text-[13px] text-text placeholder:text-subtle outline-none transition-all focus:border-accent/60 focus:ring-3 focus:ring-accent/15 disabled:opacity-60';

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cx(fieldCls, 'h-9', className)} {...rest} />;
});

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...rest },
  ref,
) {
  return <textarea ref={ref} className={cx(fieldCls, 'py-2 resize-none leading-relaxed', className)} {...rest} />;
});

export function Select<T extends string | number>({
  value,
  onChange,
  options,
  className,
  disabled,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  className?: string;
  disabled?: boolean;
}) {
  return (
    <select
      disabled={disabled}
      value={String(value)}
      onChange={(e) => {
        const o = options.find((x) => String(x.value) === e.target.value);
        if (o) onChange(o.value);
      }}
      className={cx(fieldCls, 'h-9 cursor-pointer appearance-none bg-[length:14px] bg-[right_10px_center] bg-no-repeat pr-8', className)}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")",
      }}
    >
      {options.map((o) => (
        <option key={String(o.value)} value={String(o.value)}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Field({ label, hint, children, className }: { label: React.ReactNode; hint?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <label className={cx('block', className)}>
      <div className="mb-1.5 text-[12px] font-medium text-muted">{label}</div>
      {children}
      {hint && <div className="mt-1 text-[11.5px] text-subtle">{hint}</div>}
    </label>
  );
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = 'md',
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: React.ReactNode; icon?: LucideIcon; title?: string }[];
  size?: 'sm' | 'md';
  className?: string;
}) {
  const id = useRef(`seg-${Math.random().toString(36).slice(2)}`).current;
  return (
    <div className={cx('inline-flex rounded-[10px] bg-surface-3 p-0.5', className)}>
      {options.map((o) => {
        const active = o.value === value;
        const Icon = o.icon;
        return (
          <button
            key={o.value}
            type="button"
            title={o.title}
            onClick={() => onChange(o.value)}
            className={cx(
              'relative inline-flex items-center gap-1.5 rounded-lg font-medium transition-colors cursor-pointer',
              size === 'sm' ? 'h-6 px-2 text-[11.5px]' : 'h-8 px-3 text-[12.5px]',
              active ? 'text-text' : 'text-muted hover:text-text',
            )}
          >
            {active && (
              <motion.span
                layoutId={id}
                className="absolute inset-0 rounded-lg bg-surface shadow-soft"
                transition={{ type: 'spring', stiffness: 500, damping: 38 }}
              />
            )}
            <span className="relative inline-flex items-center gap-1.5">
              {Icon && <Icon size={size === 'sm' ? 12 : 14} />}
              {o.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function Chip({
  active,
  onClick,
  children,
  color,
  className,
}: {
  active?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
  color?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        'inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-[12px] font-medium transition-all cursor-pointer',
        active ? 'border-accent/40 bg-accent/10 text-accent' : 'border-border bg-surface text-muted hover:text-text hover:border-border-strong',
        className,
      )}
      style={active && color ? { borderColor: `color-mix(in oklab, ${color} 45%, transparent)`, background: `color-mix(in oklab, ${color} 12%, transparent)`, color } : undefined}
    >
      {children}
    </button>
  );
}

// ------------------------------------------------------------------ Tabs
export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: React.ReactNode; icon?: LucideIcon; badge?: React.ReactNode }[];
  className?: string;
}) {
  const id = useRef(`tabs-${Math.random().toString(36).slice(2)}`).current;
  return (
    <div className={cx('flex items-center gap-1', className)}>
      {tabs.map((t) => {
        const active = t.value === value;
        const Icon = t.icon;
        return (
          <button
            key={t.value}
            onClick={() => onChange(t.value)}
            className={cx(
              'relative inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12.5px] font-medium transition-colors cursor-pointer',
              active ? 'text-text' : 'text-muted hover:text-text',
            )}
          >
            {active && <motion.span layoutId={id} className="absolute inset-0 rounded-lg bg-surface-3" transition={{ type: 'spring', stiffness: 500, damping: 38 }} />}
            <span className="relative inline-flex items-center gap-1.5">
              {Icon && <Icon size={14} />}
              {t.label}
              {t.badge}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Skeleton / Empty / Spinner
export function Skeleton({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return <div className={cx('skeleton rounded-lg', className)} style={style} />;
}

export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return <LoaderCircle size={size} className={cx('animate-spin text-muted', className)} />;
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
  className,
  compact,
}: {
  icon: LucideIcon;
  title: React.ReactNode;
  children?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  compact?: boolean;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      className={cx('flex flex-col items-center justify-center text-center', compact ? 'gap-2 p-4' : 'gap-3 p-8', className)}
    >
      <div className={cx('flex items-center justify-center rounded-2xl bg-accent/10 text-accent', compact ? 'h-10 w-10' : 'h-14 w-14')}>
        <Icon size={compact ? 18 : 24} />
      </div>
      <div className={cx('font-semibold text-text', compact ? 'text-[13px]' : 'text-[15px]')}>{title}</div>
      {children && <div className={cx('max-w-sm text-muted', compact ? 'text-[12px]' : 'text-[13px]')}>{children}</div>}
      {action && <div className="mt-1 flex flex-wrap items-center justify-center gap-2">{action}</div>}
    </motion.div>
  );
}

// ------------------------------------------------------------------ Tooltip (portal, pozycja fixed)
export function Tip({ text, children, side = 'top', delay = 250 }: { text: React.ReactNode; children: React.ReactElement; side?: 'top' | 'bottom' | 'right'; delay?: number }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const ref = useRef<HTMLSpanElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const show = () => {
    timer.current = setTimeout(() => {
      const el = ref.current?.firstElementChild as HTMLElement | null;
      const r = (el || ref.current)?.getBoundingClientRect();
      if (!r) return;
      if (side === 'bottom') setPos({ x: r.left + r.width / 2, y: r.bottom + 6 });
      else if (side === 'right') setPos({ x: r.right + 8, y: r.top + r.height / 2 });
      else setPos({ x: r.left + r.width / 2, y: r.top - 6 });
      setOpen(true);
    }, delay);
  };
  const hide = () => {
    if (timer.current) clearTimeout(timer.current);
    setOpen(false);
  };
  useEffect(() => () => hide(), []);
  if (!text) return children;
  const transform = side === 'bottom' ? 'translate(-50%, 0)' : side === 'right' ? 'translate(0, -50%)' : 'translate(-50%, -100%)';
  return (
    <span ref={ref} onMouseEnter={show} onMouseLeave={hide} onMouseDown={hide} className="inline-flex">
      {children}
      {createPortal(
        <AnimatePresence>
          {open && pos && (
            <motion.div
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.12 }}
              className="pointer-events-none fixed z-[200] max-w-xs rounded-lg bg-[#16161f] px-2 py-1 text-[11.5px] font-medium text-white shadow-pop dark:bg-[#2a2a38]"
              style={{ left: pos.x, top: pos.y, transform }}
            >
              {text}
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </span>
  );
}

// ------------------------------------------------------------------ Dialog
export function Dialog({
  open,
  onClose,
  title,
  subtitle,
  icon: Icon,
  children,
  footer,
  width = 560,
  dismissable = true,
}: {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: LucideIcon;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: number;
  dismissable?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && dismissable) onClose();
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose, dismissable]);
  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-[100] flex items-center justify-center p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <motion.div
            className="absolute inset-0 backdrop-blur-[3px]"
            style={{ background: 'var(--c-overlay)' }}
            onClick={() => dismissable && onClose()}
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            initial={{ opacity: 0, y: 16, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.98 }}
            transition={{ type: 'spring', stiffness: 380, damping: 32 }}
            className="relative flex max-h-[90vh] w-full flex-col overflow-hidden rounded-2xl border border-border bg-elevated shadow-pop"
            style={{ maxWidth: width }}
          >
            {(title || Icon) && (
              <div className="flex items-start gap-3 border-b border-border px-5 py-4">
                {Icon && (
                  <div className="accent-gradient flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white">
                    <Icon size={17} />
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="text-[15px] font-semibold tracking-[-0.01em]">{title}</div>
                  {subtitle && <div className="mt-0.5 text-[12.5px] text-muted">{subtitle}</div>}
                </div>
                {dismissable && <IconButton icon={X} label="Zamknij" size="sm" onClick={onClose} />}
              </div>
            )}
            <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">{children}</div>
            {footer && <div className="flex items-center justify-end gap-2 border-t border-border bg-surface-2/60 px-5 py-3">{footer}</div>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

// ------------------------------------------------------------------ Confirm (globalny)
interface ConfirmReq {
  title: string;
  text?: React.ReactNode;
  confirm?: string;
  danger?: boolean;
  input?: { placeholder?: string; initial?: string };
  resolve: (v: string | boolean | null) => void;
}
const useConfirm = create<{ req: ConfirmReq | null }>(() => ({ req: null }));

export function confirmDialog(o: Omit<ConfirmReq, 'resolve' | 'input'>): Promise<boolean> {
  return new Promise((resolve) => useConfirm.setState({ req: { ...o, resolve: (v) => resolve(!!v) } }));
}

export function promptDialog(o: Omit<ConfirmReq, 'resolve'>): Promise<string | null> {
  return new Promise((resolve) =>
    useConfirm.setState({ req: { ...o, input: o.input || {}, resolve: (v) => resolve(typeof v === 'string' ? v : null) } }),
  );
}

export function ConfirmHost() {
  const req = useConfirm((s) => s.req);
  const [val, setVal] = useState('');
  useLayoutEffect(() => {
    if (req?.input) setVal(req.input.initial || '');
  }, [req]);
  const close = (v: string | boolean | null) => {
    req?.resolve(v);
    useConfirm.setState({ req: null });
  };
  return (
    <Dialog
      open={!!req}
      onClose={() => close(null)}
      title={req?.title}
      width={440}
      footer={
        <>
          <Button variant="ghost" onClick={() => close(null)}>
            Anuluj
          </Button>
          <Button
            variant={req?.danger ? 'danger' : 'primary'}
            onClick={() => close(req?.input ? val : true)}
            autoFocus
          >
            {req?.confirm || 'Potwierdź'}
          </Button>
        </>
      }
    >
      <div className="space-y-3 px-5 py-4 text-[13px] text-muted">
        {req?.text && <div>{req.text}</div>}
        {req?.input && (
          <Input
            autoFocus
            value={val}
            placeholder={req.input.placeholder}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') close(val);
            }}
          />
        )}
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ różne
export function Stat({ label, value, sub, className }: { label: React.ReactNode; value: React.ReactNode; sub?: React.ReactNode; className?: string }) {
  return (
    <div className={cx('min-w-0', className)}>
      <div className="text-[11.5px] font-medium text-muted">{label}</div>
      <div className="tabular mt-0.5 truncate text-[15px] font-semibold tracking-[-0.01em] text-text">{value}</div>
      {sub && <div className="mt-0.5 truncate text-[11.5px] text-subtle">{sub}</div>}
    </div>
  );
}

export function SectionTitle({ children, right, className }: { children: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cx('mb-3 flex items-center justify-between gap-3', className)}>
      <h2 className="text-[13px] font-semibold uppercase tracking-[0.06em] text-muted">{children}</h2>
      {right}
    </div>
  );
}

export function PageHeader({ title, subtitle, right, icon: Icon }: { title: React.ReactNode; subtitle?: React.ReactNode; right?: React.ReactNode; icon?: LucideIcon }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="flex items-center gap-3">
        {Icon && (
          <div className="accent-gradient flex h-10 w-10 items-center justify-center rounded-xl text-white shadow-card">
            <Icon size={19} />
          </div>
        )}
        <div>
          <h1 className="text-[22px] font-semibold tracking-[-0.02em] text-text">{title}</h1>
          {subtitle && <p className="mt-0.5 text-[13px] text-muted">{subtitle}</p>}
        </div>
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  );
}
