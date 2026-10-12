import { describe, expect, it } from "vitest";

import {
  formatInMoscow,
  formatMoscowShortDateTime,
  getMoscowCalendarDay,
} from "./dateTime";

describe("Moscow date formatting", () => {
  it("converts UTC timestamps to Europe/Moscow", () => {
    expect(
      formatInMoscow(
        "2026-04-04T21:30:00Z",
        {
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hourCycle: "h23",
        },
        "sv-SE"
      )
    ).toBe("2026-04-05 00:30");
  });

  it("uses the Moscow calendar day across the UTC date boundary", () => {
    expect(getMoscowCalendarDay("2026-04-04T21:30:00Z")).toBe(
      getMoscowCalendarDay("2026-04-05T12:00:00+03:00")
    );
  });

  it("builds short timestamps without environment-specific Intl literals", () => {
    expect(formatMoscowShortDateTime("2026-07-23T06:01:00Z")).toBe(
      "23 июл., 09:01"
    );
    expect(formatMoscowShortDateTime("2026-09-01T13:27:00Z")).toBe(
      "01 сент., 16:27"
    );
  });
});
