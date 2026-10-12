"use client";

import { useEffect } from "react";
import { driver, type Driver, type DriveStep } from "driver.js";
import "driver.js/dist/driver.css";
import "./app-tour.css";

const TOUR_DONE_KEY = "rhetorica.tour.done";

const RHETORICA_DOCS_URL =
  "https://example.com/docs/rhetorica";

const SPARKLES_HTML = `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="driver-tour-sparkles" aria-hidden="true"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/><path d="M5 3v4"/><path d="M19 17v4"/><path d="M3 5h4"/><path d="M17 19h4"/></svg>`;

function elementVisible(selector: string): boolean {
  const element = document.querySelector(selector);
  return element ? element.getClientRects().length > 0 : false;
}

function buildSteps(): DriveStep[] {
  const dockToggleVisible = elementVisible('[data-tour="tour-composer-dock-toggle"]');

  const steps: DriveStep[] = [
    {
      popover: {
        popoverClass: "rhetorica-tour-popover rhetorica-tour-popover-wide",
        title: `${SPARKLES_HTML}Добро пожаловать в тур по Rhetorica!`,
        description:
          "За несколько шагов разберём основные возможности Rhetorica и покажем, как получать от неё максимум" +
          '<br/><span class="tour-popover-hint">Подсказка: шаги тура можно переключать стрелками ← → на клавиатуре</span>',
      },
    },
    {
      element: '[data-tour="agent-menu"]',
      popover: {
        popoverClass: "rhetorica-tour-popover",
        title: `${SPARKLES_HTML}Выбор агента`,
        description:
          "Выберите агента под свою задачу — каждый специализируется на своём типе работы и помогает получать более точные ответы",
        side: "top",
        align: "center",
      },
    },
    {
      element: '[data-tour="tour-chat-input"]',
      popover: {
        popoverClass: "rhetorica-tour-popover",
        title: `${SPARKLES_HTML}Ваш запрос`,
        description:
          "Опишите, что вам нужно, обычным языком. Чем точнее контекст и задача, тем полезнее будет ответ",
        side: "top",
        align: "start",
      },
    },
    {
      element: '[data-tour="tour-chat-thread"]',
      popover: {
        popoverClass: "rhetorica-tour-popover",
        title: `${SPARKLES_HTML}Окно чата`,
        description:
          "Здесь проходит весь диалог с агентом: задавайте дополнительные вопросы, уточняйте детали и продолжайте диалог, не теряя контекст",
        side: "top",
        align: "start",
      },
    },
    {
      element: '[data-tour="tour-sidebar"]',
      popover: {
        popoverClass: "rhetorica-tour-popover",
        title: `${SPARKLES_HTML}Ваши чаты`,
        description:
          "Все диалоги собраны здесь. Возвращайтесь к прошлым чатам, переключайтесь между ними и продолжайте работу с того места, где остановились",
        side: "right",
        align: "center",
      },
    },
    {
      element: '[aria-label="Тема интерфейса"]',
      popover: {
        popoverClass: "rhetorica-tour-popover",
        title: `${SPARKLES_HTML}Тема`,
        description: "Выберите удобную тему — светлую или тёмную",
        side: "left",
        align: "center",
      },
    },
    {
      element: '[aria-label="Ширина чата"]',
      popover: {
        popoverClass: "rhetorica-tour-popover",
        title: `${SPARKLES_HTML}Ширина просмотра`,
        description: "Любите пошире? Увеличьте ширину области просмотра чата",
        side: "left",
        align: "center",
      },
    },
    {
      element: dockToggleVisible
        ? '[data-tour="tour-composer-dock-toggle"]'
        : '[data-tour="tour-composer-dock"]',
      popover: {
        popoverClass: "rhetorica-tour-popover",
        title: `${SPARKLES_HTML}Область ввода`,
        description:
          "Больше пространства для мысли? Расположите область ввода вертикально и изменяйте её размер, потянув за границу",
        side: "bottom",
        align: "center",
      },
    },
  ];

  const scenarioCatalogVisible = elementVisible('[data-tour="tour-scenario-catalog"]');

  steps.push({
    element: scenarioCatalogVisible ? '[data-tour="tour-scenario-catalog"]' : undefined,
    popover: {
      popoverClass: "rhetorica-tour-popover",
      title: `${SPARKLES_HTML}Сценарии`,
      description: scenarioCatalogVisible
        ? "Используйте готовые сценарии для типовых задач или создавайте свои — так повторяющуюся работу можно запускать в несколько кликов.<br/>Выбрать сценарий можно при создании нового чата"
        : "Готовые сценарии закрывают типовые задачи — запускайте повторяющуюся работу в несколько кликов, не переписывая запросы заново. Выбрать сценарий можно при создании нового чата: откройте новый чат и выберите нужный из каталога или создайте свой",
      side: scenarioCatalogVisible ? "bottom" : undefined,
      align: "center",
    },
  });

  steps.push({
    element: '[data-tour="tour-guides-panel"]',
    popover: {
      popoverClass: "rhetorica-tour-popover",
      title: `${SPARKLES_HTML}Полезные инструкции`,
      description:
        "Здесь вы найдёте полезные инструкции, рекомендации и материалы на разные случаи",
      side: "top",
      align: "center",
    },
  });

  steps.push({
    popover: {
      popoverClass: "rhetorica-tour-popover rhetorica-tour-popover-wide",
      title: `${SPARKLES_HTML}Тур по Rhetorica завершён!`,
      description:
        "Спасибо, что прошли тур!<br/>" +
        'Будем рады вашим идеям и замечаниям по приложению — <a href="https://mattermost.example.com/data-management/channels/data-governance-product" target="_blank" rel="noreferrer">поделитесь обратной связью</a><br/><br/>' +
        `Подробная информация о Rhetorica — смотрите <a href="${RHETORICA_DOCS_URL}" target="_blank" rel="noreferrer">в Документации</a>`,
    },
  });

  return steps;
}

let driverObj: Driver | null = null;

function isLightTheme(): boolean {
  if (typeof document === "undefined") return false;
  return document.documentElement.getAttribute("data-theme") === "light";
}

function themeOverlayConfig() {
  return isLightTheme()
    ? { overlayColor: "#000000", overlayOpacity: 0.7 }
    : { overlayColor: "#ffffff", overlayOpacity: 0.32 };
}

function createTourDriver(): Driver {
  return driver({
    ...themeOverlayConfig(),
    showProgress: true,
    progressText: "{{current}} из {{total}}",
    nextBtnText: "Далее",
    prevBtnText: "Назад",
    doneBtnText: "Готово",
    steps: buildSteps(),
    onDoneClick: () => {
      window.localStorage.setItem(TOUR_DONE_KEY, "1");
      driverObj?.destroy();
    },
    onCloseClick: () => {
      window.localStorage.setItem(TOUR_DONE_KEY, "1");
      driverObj?.destroy();
    },
  });
}

export function startAppTour() {
  if (driverObj?.isActive()) {
    return;
  }
  const next = createTourDriver();
  driverObj?.destroy();
  driverObj = next;
  next.drive();
}

export function AppTour() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.localStorage.getItem(TOUR_DONE_KEY)) return;

    const tour = createTourDriver();
    driverObj?.destroy();
    driverObj = tour;
    tour.drive();

    return () => {
      if (tour.isActive()) {
        tour.destroy();
      }
      if (driverObj === tour) {
        driverObj = null;
      }
    };
  }, []);

  return null;
}
