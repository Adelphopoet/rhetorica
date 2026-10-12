import { create } from "zustand";

type GuidesPanelState = {
  guidesPanelOpen: boolean;
  openGuidesPanel: () => void;
  closeGuidesPanel: () => void;
  toggleGuidesPanel: () => void;
};

export const useGuidesPanelStore = create<GuidesPanelState>()((set) => ({
  guidesPanelOpen: false,
  openGuidesPanel: () => set({ guidesPanelOpen: true }),
  closeGuidesPanel: () => set({ guidesPanelOpen: false }),
  toggleGuidesPanel: () =>
    set((state) => ({ guidesPanelOpen: !state.guidesPanelOpen })),
}));
