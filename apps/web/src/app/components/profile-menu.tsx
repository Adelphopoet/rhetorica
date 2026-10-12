"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Avatar, Button, cn, Tooltip, useToast } from "@rhetorica/ui";
import { BookOpenText, ChevronDown, KeyRound, LoaderCircle, LogOut, ShieldCheck, User, Wifi, WifiOff } from "lucide-react";
import { ApiTokenDialog } from "./api-token-dialog";

const RHETORICA_DOCS_URL =
  "https://example.com/docs/rhetorica";

type ProfileMenuProps = {
  workspaceName: string;
  apiStatus: string;
  login: string;
  displayName: string | null;
  role: string;
  authProvider: string;
  canAccessAdmin: boolean;
  rootClassName?: string;
  triggerClassName?: string;
  contentClassName?: string;
  forceDetailsVisible?: boolean;
  openUp?: boolean;
  openSignal?: number;
  onOpenChange?: (isOpen: boolean) => void;
};

export function getDisplayName(displayName: string | null, login: string): string {
  const normalizedName = displayName?.trim();
  if (normalizedName) {
    return normalizedName;
  }

  return login;
}

export function getInitials(name: string): string {
  const parts = name
    .split(/\s+/)
    .map((part) => part.trim())
    .filter(Boolean)
    .slice(0, 2);

  if (parts.length === 0) {
    return "RH";
  }

  return parts.map((part) => part[0]?.toUpperCase() ?? "").join("");
}

const ROLE_LABELS: Record<string, string> = {
  admin: "Админ",
  analyst: "Аналитик",
};

const AUTH_PROVIDER_LABELS: Record<string, string> = {
  password: "Пароль",
};

function toLabel(value: string): string {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function toRoleLabel(value: string): string {
  const normalized = value.trim().toLowerCase();
  return ROLE_LABELS[normalized] ?? toLabel(value);
}

function toProviderLabel(value: string): string {
  const normalized = value.trim().toLowerCase();
  return AUTH_PROVIDER_LABELS[normalized] ?? toLabel(value);
}

function stableIdSegment(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9_-]+/g, "-") || "user";
}

export function ProfileMenu({
  workspaceName,
  apiStatus,
  login,
  displayName,
  role,
  authProvider,
  canAccessAdmin,
  rootClassName,
  triggerClassName,
  contentClassName,
  forceDetailsVisible = false,
  openUp = false,
  openSignal = 0,
  onOpenChange,
}: ProfileMenuProps) {
  const { addToast } = useToast();
  const [isOpen, setIsOpen] = useState(false);
  const [tokensOpen, setTokensOpen] = useState(false);
  const [logoutPending, setLogoutPending] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuId = `profile-menu-${stableIdSegment(workspaceName)}-${stableIdSegment(login)}`;
  const shouldReduceMotion = useReducedMotion();
  const resolvedDisplayName = getDisplayName(displayName, login);
  const initials = getInitials(resolvedDisplayName);
  const roleLabel = toRoleLabel(role);
  const providerLabel = toProviderLabel(authProvider);
  const isOnline = apiStatus !== "offline";

  const getMenuItems = useCallback(
    () => Array.from(rootRef.current?.querySelectorAll<HTMLElement>("[data-profile-menu-item='true']") ?? []),
    []
  );

  const focusMenuItem = useCallback((index: number) => {
    const items = getMenuItems();
    const target = items[index];
    if (target) {
      target.focus();
    }
  }, [getMenuItems]);

  const closeMenu = useCallback((returnFocus: boolean) => {
    setIsOpen(false);
    if (returnFocus) {
      window.setTimeout(() => triggerRef.current?.focus(), 0);
    }
  }, []);

  const handleDocumentPointerDown = useCallback((event: PointerEvent) => {
    const target = event.target;
    if (target instanceof Node && !rootRef.current?.contains(target)) {
      closeMenu(false);
    }
  }, [closeMenu]);

  const handleDocumentKeyDown = useCallback((event: KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeMenu(true);
    }
  }, [closeMenu]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    const frameId = window.requestAnimationFrame(() => focusMenuItem(0));
    document.addEventListener("pointerdown", handleDocumentPointerDown);
    document.addEventListener("keydown", handleDocumentKeyDown);

    return () => {
      window.cancelAnimationFrame(frameId);
      document.removeEventListener("pointerdown", handleDocumentPointerDown);
      document.removeEventListener("keydown", handleDocumentKeyDown);
    };
  }, [focusMenuItem, handleDocumentKeyDown, handleDocumentPointerDown, isOpen]);

  useEffect(() => {
    if (openSignal > 0) {
      setIsOpen(true);
    }
  }, [openSignal]);

  useEffect(() => {
    onOpenChange?.(isOpen);
  }, [isOpen, onOpenChange]);

  const handleLogout = async () => {
    if (logoutPending) {
      return;
    }

    setLogoutPending(true);
    try {
      const response = await fetch("/api/auth/logout", {
        method: "POST",
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error("logout_failed");
      }
      const body = (await response.json()) as { logout_url?: string | null };

      closeMenu(false);
      window.location.assign(body.logout_url || "/login");
    } catch {
      setLogoutPending(false);
      addToast({
        type: "error",
        title: "Не удалось выйти",
        description: "Не удалось завершить сессию",
      });
    }
  };

  return (
    <div ref={rootRef} className={cn("relative", rootClassName)}>
      <Button
        ref={triggerRef}
        type="button"
        variant="ghost"
        size="sm"
        aria-label="Открыть меню профиля"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-controls={menuId}
        onClick={() => {
          setIsOpen((current) => !current);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setIsOpen(true);
          }
        }}
        className={cn(
          "relative z-40 h-11 rounded-[20px] border border-border-strong bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.92))] px-2.5 text-primary shadow-[0_10px_30px_rgb(var(--color-shadow)/calc(0.18*var(--shadow-opacity-scale)))] hover:border-border-strong hover:bg-elevated hover:text-primary",
          triggerClassName
        )}
      >
        <Avatar
          size="sm"
          fallback={initials}
          className="border border-border-strong bg-[radial-gradient(circle_at_top,rgb(var(--color-accent)/0.32),transparent_65%),rgb(var(--color-elevated))] text-primary"
        />
        <div className={cn("min-w-0 text-left", forceDetailsVisible ? "block flex-1" : "hidden sm:block")}>
          <p className="max-w-32 truncate text-sm font-semibold text-primary">
            {resolvedDisplayName}
          </p>
          <p className="truncate text-[11px] uppercase tracking-[0.16em] text-muted">
            {roleLabel}
          </p>
        </div>
        <ChevronDown
          className={cn(
            "h-4 w-4 text-accent transition-transform duration-200",
            isOpen ? "rotate-0" : "rotate-180"
          )}
        />
      </Button>

      <AnimatePresence initial={false}>
        {isOpen ? (
          <motion.div
            id={menuId}
            role="menu"
            aria-label="Меню профиля"
            initial={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: openUp ? 10 : -10, scale: 0.96 }}
            animate={shouldReduceMotion ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
            exit={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: openUp ? 8 : -8, scale: 0.98 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
            onKeyDown={(event) => {
              const items = getMenuItems();
              if (items.length === 0) {
                return;
              }

              const currentIndex = items.findIndex((item) => item === document.activeElement);

              if (event.key === "ArrowDown") {
                event.preventDefault();
                const nextIndex = currentIndex === -1 ? 0 : (currentIndex + 1) % items.length;
                focusMenuItem(nextIndex);
              }

              if (event.key === "ArrowUp") {
                event.preventDefault();
                const nextIndex =
                  currentIndex === -1 ? items.length - 1 : (currentIndex - 1 + items.length) % items.length;
                focusMenuItem(nextIndex);
              }

              if (event.key === "Home") {
                event.preventDefault();
                focusMenuItem(0);
              }

              if (event.key === "End") {
                event.preventDefault();
                focusMenuItem(items.length - 1);
              }
            }}
            className={cn(
              "absolute right-0 z-50 w-[min(92vw,320px)] rounded-[18px] border border-border-strong bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.98),rgb(var(--color-surface)/0.98))] p-2 shadow-[0_24px_70px_rgb(var(--color-shadow)/calc(0.42*var(--shadow-opacity-scale)))] backdrop-blur-2xl",
              openUp ? "bottom-[calc(100%+12px)]" : "top-[calc(100%+12px)]",
              contentClassName
            )}
          >
            <div className="p-2">
              <p className="truncate text-sm font-medium text-primary">
                {login}
              </p>

              <div className="mt-3 flex items-center gap-4">
                {canAccessAdmin ? (
                  <Tooltip content={`API: ${apiStatus}`} side="top">
                    <span
                      className={cn(
                        "flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border",
                        isOnline
                          ? "border-success-border bg-success-subtle"
                          : "border-danger-border bg-danger-subtle"
                      )}
                    >
                      {isOnline ? (
                        <Wifi className="h-4 w-4 text-success" />
                      ) : (
                        <WifiOff className="h-4 w-4 text-danger" />
                      )}
                    </span>
                  </Tooltip>
                ) : null}

                <div className="flex items-center gap-2 text-sm text-secondary">
                  <User className="h-4 w-4 text-secondary" />
                  {roleLabel}
                </div>

                {canAccessAdmin ? (
                  <div className="flex items-center gap-2 text-sm text-secondary">
                    <KeyRound className="h-4 w-4 text-secondary" />
                    {providerLabel}
                  </div>
                ) : null}
              </div>
            </div>

            {canAccessAdmin ? (
              <Link
                href="/admin"
                role="menuitem"
                data-profile-menu-item="true"
                onClick={() => closeMenu(false)}
                className="mt-2 flex w-full items-center gap-3 rounded-[18px] px-3 py-3 text-sm font-medium text-primary transition hover:bg-elevated hover:text-primary focus:outline-none focus:ring-2 focus:ring-accent"
              >
                <ShieldCheck className="h-4 w-4 text-accent" />
                Администрирование
              </Link>
            ) : null}

            <button
              type="button"
              role="menuitem"
              data-profile-menu-item="true"
              onClick={() => {
                closeMenu(false);
                setTokensOpen(true);
              }}
              className="mt-2 flex w-full items-center gap-3 rounded-[18px] px-3 py-3 text-sm font-medium text-primary transition hover:bg-elevated hover:text-primary focus:outline-none focus:ring-2 focus:ring-accent"
            >
              <KeyRound className="h-4 w-4 text-accent" />
              API &amp; MCP токены
            </button>

            <a
              href={RHETORICA_DOCS_URL}
              target="_blank"
              rel="noreferrer"
              role="menuitem"
              data-profile-menu-item="true"
              onClick={() => closeMenu(false)}
              className="mt-2 flex w-full items-center gap-3 rounded-[18px] px-3 py-3 text-sm font-medium text-primary transition hover:bg-elevated hover:text-primary focus:outline-none focus:ring-2 focus:ring-accent"
            >
              <BookOpenText className="h-4 w-4 text-accent" />
              Документация
            </a>

            <button
              type="button"
              role="menuitem"
              data-profile-menu-item="true"
              onClick={() => {
                void handleLogout();
              }}
              disabled={logoutPending}
              className="mt-2 flex w-full items-center gap-3 rounded-[18px] border border-purple-border bg-[linear-gradient(180deg,rgb(var(--color-danger-subtle)/0.95),rgb(var(--color-danger-subtle)/0.95))] px-3 py-3 text-sm font-medium text-danger transition hover:border-danger-border hover:bg-purple-subtle hover:text-primary focus:outline-none focus:ring-2 focus:ring-danger disabled:cursor-not-allowed disabled:opacity-70"
            >
              {logoutPending ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <LogOut className="h-4 w-4" />
              )}
              Выйти
            </button>
          </motion.div>
        ) : null}
      </AnimatePresence>
      <ApiTokenDialog open={tokensOpen} onOpenChange={setTokensOpen} />
    </div>
  );
}
