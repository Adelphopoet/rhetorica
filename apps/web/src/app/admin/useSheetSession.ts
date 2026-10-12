"use client";

import { useCallback, useState, type Dispatch, type SetStateAction } from "react";

// Session identity for draft-preserving sheets. The nonce stays the same while
// a form is being filled in (so closing and reopening keeps the draft) and is
// bumped once after a successful save (so the next open starts fresh).
export function useSessionNonce(): readonly [number, () => void] {
  const [nonce, setNonce] = useState(0);
  const bump = useCallback(() => setNonce((current) => current + 1), []);
  return [nonce, bump] as const;
}

// State that re-initializes when the sheet session changes but survives plain
// close/reopen cycles. `sessionKey` is the same value that remounts the form,
// so local selections reset exactly when the form itself starts over.
export function useSessionState<S>(
  sessionKey: string,
  createInitial: () => S
): readonly [S, Dispatch<SetStateAction<S>>] {
  const [state, setState] = useState<S>(createInitial);
  const [activeKey, setActiveKey] = useState(sessionKey);
  if (sessionKey !== activeKey) {
    setActiveKey(sessionKey);
    setState(createInitial());
  }
  return [state, setState];
}