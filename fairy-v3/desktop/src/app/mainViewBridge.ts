import type { SettingsCategoryId } from "../core/client";
import type { InvokeFunction } from "../core/tauriTransport";

export type MainView = "workspace" | "settings";

export interface MainViewRequest {
  schema_version: 1;
  sequence: number;
  view: MainView;
  settings_category: SettingsCategoryId | null;
  conversation_id: string | null;
  turn_id?: string | null;
}

export interface MainViewHost {
  get(): Promise<MainViewRequest>;
  navigate(
    view: MainView,
    settingsCategory?: SettingsCategoryId,
    conversationId?: string,
  ): Promise<MainViewRequest>;
  subscribe(listener: (request: MainViewRequest) => void): Promise<() => void>;
}

type MainViewSubscriber = (
  listener: (request: MainViewRequest) => void,
) => Promise<() => void>;

export class TauriMainViewHost implements MainViewHost {
  constructor(
    private readonly invoke: InvokeFunction,
    private readonly subscriber: MainViewSubscriber,
  ) {}

  get(): Promise<MainViewRequest> {
    return this.invoke("main_view_request_get");
  }

  navigate(
    view: MainView,
    settingsCategory?: SettingsCategoryId,
    conversationId?: string,
  ): Promise<MainViewRequest> {
    return this.invoke("main_view_navigate", {
      input: {
        view,
        settings_category: settingsCategory ?? null,
        conversation_id: conversationId ?? null,
      },
    });
  }

  subscribe(listener: (request: MainViewRequest) => void): Promise<() => void> {
    return this.subscriber(listener);
  }
}
