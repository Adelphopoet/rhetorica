"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Avatar, Badge, Button, cn, useToast } from "@rhetorica/ui";
import { BookOpenText, ChevronDown, LoaderCircle, LogOut, ShieldCheck } from "lucide-react";

type ProfileMenuProps = {
  workspaceName: string;
  login: string;
  displayName: string | null;
  role: string;
  authProvider: string;
  canAccessAdmin: boolean;
  rootClassName?: string;
  triggerClassName?: string;
  contentClassName?: string;
  forceDetailsVisible?: boolean;
};

function getDisplayName(displayName: string | null, login: string): string {
  const normalizedName = displayName?.trim();
  if (normalizedName) {
    return normalizedName;
  }

  return login;
}

function getInitials(name: string): string {
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

function toLabel(value: string): string {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

export function ProfileMenu({
  workspaceName,
  login,
  displayName,
  role,
  authProvider,
  canAccessAdmin,
  rootClassName,
  triggerClassName,
  contentClassName,
  forceDetailsVisible = false,
}: ProfileMenuProps) {
  const { addToast } = useToast();
  const [isOpen, setIsOpen] = useState(false);
  const [logoutPending, setLogoutPending] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuId = useId();
  const shouldReduceMotion = useReducedMotion();
  const resolvedDisplayName = getDisplayName(displayName, login);
  const initials = getInitials(resolvedDisplayName);
  const roleLabel = toLabel(role);
  const providerLabel = toLabel(authProvider);

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

      closeMenu(false);
      window.location.assign("/login");
    } catch {
      setLogoutPending(false);
      addToast({
        type: "error",
        title: "Logout failed",
        description: "Session could not be closed.",
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
        aria-label="Open profile menu"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-controls={menuId}
        onClick={() => {
          setIsOpen((current) => !current);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setIsOpen(true);
          }
        }}
        className={cn(
          "h-11 rounded-[20px] border border-[#23324a] bg-[linear-gradient(180deg,rgba(10,18,31,0.98),rgba(10,16,28,0.92))] px-2.5 text-[#d7e6ff] shadow-[0_10px_30px_rgba(0,0,0,0.18)] hover:border-[#335077] hover:bg-[#132033] hover:text-white",
          triggerClassName
        )}
      >
        <Avatar
          size="sm"
          fallback={initials}
          className="border border-[#31496c] bg-[radial-gradient(circle_at_top,rgba(93,168,255,0.32),transparent_65%),#101a2c] text-[#eef4ff]"
        />
        <div className={cn("min-w-0 text-left", forceDetailsVisible ? "block flex-1" : "hidden sm:block")}>
          <p className="max-w-32 truncate text-sm font-semibold text-[#eef4ff]">
            {resolvedDisplayName}
          </p>
          <p className="truncate text-[11px] uppercase tracking-[0.16em] text-[#7487a7]">
            {roleLabel}
          </p>
        </div>
        <ChevronDown
          className={cn(
            "h-4 w-4 text-[#8ba9d6] transition-transform duration-200",
            isOpen ? "rotate-180" : "rotate-0"
          )}
        />
      </Button>

      <AnimatePresence initial={false}>
        {isOpen ? (
          <motion.div
            id={menuId}
            role="menu"
            aria-label="Profile menu"
            initial={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: -10, scale: 0.96 }}
            animate={shouldReduceMotion ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
            exit={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.98 }}
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
              "absolute right-0 top-[calc(100%+12px)] z-50 w-[min(92vw,320px)] overflow-hidden rounded-[24px] border border-[#24344f] bg-[linear-gradient(180deg,rgba(10,16,29,0.98),rgba(7,12,22,0.98))] p-2 shadow-[0_24px_70px_rgba(0,0,0,0.42)] backdrop-blur-2xl",
              contentClassName
            )}
          >
            <div className="rounded-[18px] border border-[#1e2f49] bg-[radial-gradient(circle_at_top_right,rgba(93,168,255,0.18),transparent_35%),linear-gradient(180deg,rgba(11,18,31,0.96),rgba(9,14,25,0.92))] p-3">
              <div className="flex items-center gap-3">
                <Avatar
                  size="md"
                  fallback={initials}
                  className="border border-[#37527a] bg-[radial-gradient(circle_at_top,rgba(93,168,255,0.4),transparent_65%),#101a2c] text-[#f4f8ff]"
                />
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-[#eef4ff]">
                    {resolvedDisplayName}
                  </p>
                  <p className="truncate text-xs text-[#9bb0d1]">
                    @{login}
                  </p>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Badge variant={canAccessAdmin ? "primary" : "default"} className="px-2.5 py-1">
                  {roleLabel}
                </Badge>
                <Badge variant="default" className="px-2.5 py-1 text-[#cfe0ff]">
                  {providerLabel}
                </Badge>
              </div>

              <div className="mt-3 rounded-2xl border border-[#1f314c] bg-[#0b1322]/80 px-3 py-2">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#6880a6]">
                  Workspace
                </p>
                <p className="mt-1 truncate text-sm text-[#eef4ff]">
                  {workspaceName}
                </p>
              </div>
            </div>

            {canAccessAdmin ? (
              <Link
                href="/admin"
                role="menuitem"
                data-profile-menu-item="true"
                onClick={() => closeMenu(false)}
                className="mt-2 flex w-full items-center gap-3 rounded-[18px] px-3 py-3 text-sm font-medium text-[#dce8fb] transition hover:bg-[#12203a] hover:text-white focus:outline-none focus:ring-2 focus:ring-[#5da8ff]"
              >
                <ShieldCheck className="h-4 w-4 text-[#8eb6ff]" />
                Admin console
              </Link>
            ) : null}

            <Link
              href="/docs"
              role="menuitem"
              data-profile-menu-item="true"
              onClick={() => closeMenu(false)}
              className="mt-2 flex w-full items-center gap-3 rounded-[18px] px-3 py-3 text-sm font-medium text-[#dce8fb] transition hover:bg-[#12203a] hover:text-white focus:outline-none focus:ring-2 focus:ring-[#5da8ff]"
            >
              <BookOpenText className="h-4 w-4 text-[#8eb6ff]" />
              Docs
            </Link>

            <button
              type="button"
              role="menuitem"
              data-profile-menu-item="true"
              onClick={() => {
                void handleLogout();
              }}
              disabled={logoutPending}
              className="mt-2 flex w-full items-center gap-3 rounded-[18px] border border-[#3b2331] bg-[linear-gradient(180deg,rgba(39,17,25,0.95),rgba(29,12,19,0.95))] px-3 py-3 text-sm font-medium text-[#ffd5dc] transition hover:border-[#6b3145] hover:bg-[#351623] hover:text-white focus:outline-none focus:ring-2 focus:ring-[#ff7d93] disabled:cursor-not-allowed disabled:opacity-70"
            >
              {logoutPending ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <LogOut className="h-4 w-4" />
              )}
              Log out
            </button>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
