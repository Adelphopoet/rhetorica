import { create } from "zustand";

type AgentMenuState = {
  agentMenuOpen: boolean;
  agentMenuPulse: boolean;
  openAgentMenu: () => void;
  closeAgentMenu: () => void;
  toggleAgentMenu: () => void;
  toggleAgentMenuWithPulse: () => void;
};

export const useAgentMenuStore = create<AgentMenuState>()((set) => ({
  agentMenuOpen: false,
  agentMenuPulse: false,
  openAgentMenu: () => set({ agentMenuOpen: true, agentMenuPulse: false }),
  closeAgentMenu: () => set({ agentMenuOpen: false, agentMenuPulse: false }),
  toggleAgentMenu: () =>
    set((state) => ({ agentMenuOpen: !state.agentMenuOpen, agentMenuPulse: false })),
  toggleAgentMenuWithPulse: () =>
    set((state) => ({ agentMenuOpen: !state.agentMenuOpen, agentMenuPulse: !state.agentMenuOpen })),
}));