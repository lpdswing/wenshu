import { useEffect, useState } from "react";
import {
  connectConnector,
  disconnectConnector,
  getWechatSettings,
  patchWechatSettings,
  type WechatSettings,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { Toggle } from "../Toggle";
import type { DetailProps } from "./ConnectorsSection";
import { FOOT, GRP, GRP_H, PILL_ACCENT, ROW } from "./ui";

const INPUT =
  "w-full px-3 py-2 rounded-lg border border-line bg-panel text-[13.5px] text-ink outline-none focus:border-accent";
const LABEL = "block text-[12px] text-muted mb-1";

export function WechatDetail({ c, cloud: _cloud, slack: _slack, onChanged }: DetailProps) {
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [settings, setSettings] = useState<WechatSettings | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);

  useEffect(() => {
    let active = true;
    if (!c.connected) {
      setSettings(null);
      setSettingsLoading(false);
      setSettingsError(null);
      return () => {
        active = false;
      };
    }

    setSettingsLoading(true);
    setSettingsError(null);
    getWechatSettings()
      .then((value) => {
        if (active) setSettings(value);
      })
      .catch(() => {
        if (active) setSettingsError("无法读取评论设置，请稍后重试。");
      })
      .finally(() => {
        if (active) setSettingsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [c.connected]);

  const connect = async () => {
    if (!appId.trim() || !appSecret) return;
    setConnecting(true);
    setConnectError(null);
    try {
      const result = await connectConnector("wechat_official", {
        app_id: appId.trim(),
        app_secret: appSecret,
      });
      if (!result.ok) {
        setConnectError(result.error || "无法验证公众号凭据。");
        return;
      }
      // Never retain or render the submitted secret after a successful connection.
      setAppSecret("");
      setAppId("");
      onChanged();
    } catch {
      setConnectError("无法连接公众号，请检查网络后重试。");
    } finally {
      setConnecting(false);
    }
  };

  const saveSettings = async (next: WechatSettings) => {
    if (settingsSaving) return;
    setSettingsSaving(true);
    setSettingsError(null);
    try {
      const saved = await patchWechatSettings(next);
      setSettings(saved);
    } catch {
      // `settings` remains the last server-confirmed value: a failed save never
      // leaves either switch showing a state that was not persisted.
      setSettingsError("保存失败，设置未更改，请重试。");
    } finally {
      setSettingsSaving(false);
    }
  };

  const retrySettings = async () => {
    setSettingsLoading(true);
    setSettingsError(null);
    try {
      setSettings(await getWechatSettings());
    } catch {
      setSettingsError("无法读取评论设置，请稍后重试。");
    } finally {
      setSettingsLoading(false);
    }
  };

  return (
    <div data-testid="wechat-detail">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title={c.title} />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">微信公众号</h2>
          <div className="text-[12.5px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span className="w-2 h-2 rounded-full bg-ok" />
                <span data-testid="wechat-identity">已连接 · {c.identity || "公众号账号"}</span>
              </>
            ) : (
              <span>未连接</span>
            )}
          </div>
        </div>
        {c.connected && (
          <button
            className="text-[12.5px] text-danger/80 hover:text-danger shrink-0 disabled:opacity-50"
            data-testid="wechat-disconnect"
            disabled={disconnecting}
            onClick={async () => {
              setDisconnecting(true);
              try {
                await disconnectConnector("wechat_official");
                onChanged();
              } finally {
                setDisconnecting(false);
              }
            }}
          >
            {disconnecting ? "正在断开…" : "断开连接"}
          </button>
        )}
      </div>

      {!c.connected ? (
        <form
          data-testid="wechat-connect-form"
          onSubmit={(event) => {
            event.preventDefault();
            void connect();
          }}
        >
          <p className="text-[13px] text-ink/90 leading-relaxed mb-4 px-0.5">
            连接后，文枢可以把已确认的图文保存到公众号草稿箱，不会自动发表或群发。
          </p>

          {c.instructions.length > 0 && (
            <ol className="list-decimal pl-5 text-[12.5px] text-muted leading-relaxed space-y-1 mb-4">
              {c.instructions.map((step, index) => (
                <li key={index}>{step}</li>
              ))}
            </ol>
          )}

          <div className="space-y-3">
            <label className="block">
              <span className={LABEL}>AppID</span>
              <input
                className={INPUT}
                data-testid="wechat-app-id"
                type="text"
                value={appId}
                placeholder="wx…"
                autoCapitalize="none"
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => {
                  setAppId(event.target.value);
                  setConnectError(null);
                }}
              />
            </label>
            <label className="block">
              <span className={LABEL}>AppSecret</span>
              <input
                className={INPUT}
                data-testid="wechat-app-secret"
                type="password"
                value={appSecret}
                placeholder="请输入 AppSecret"
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => {
                  setAppSecret(event.target.value);
                  setConnectError(null);
                }}
              />
            </label>
          </div>

          <div className="flex items-center gap-3 mt-4">
            <button
              className={PILL_ACCENT}
              data-testid="wechat-connect"
              type="submit"
              disabled={connecting || !appId.trim() || !appSecret}
            >
              {connecting ? "正在验证…" : "连接公众号"}
            </button>
            <span className="text-[11.5px] text-faint">凭据只保存在这台电脑上。</span>
          </div>
          <div className="min-h-[20px] mt-2 text-[12.5px] text-danger" role="alert" data-testid="wechat-connect-error">
            {connectError}
          </div>
        </form>
      ) : (
        <>
          <div className={GRP_H + " !mt-0"}>评论设置</div>
          <div className={GRP} data-testid="wechat-settings">
            {settingsLoading && !settings ? (
              <div className={ROW + " text-[12.5px] text-muted"}>正在读取设置…</div>
            ) : settings ? (
              <>
                <div className={ROW + " justify-between"}>
                  <span className="min-w-0 pr-4">
                    <span className="block text-[13px] font-medium">开启评论</span>
                    <span className="block text-[12px] text-muted">草稿保存后允许读者评论。</span>
                  </span>
                  <Toggle
                    checked={settings.need_open_comment}
                    disabled={settingsSaving}
                    title="开启评论"
                    onChange={(need_open_comment) =>
                      void saveSettings({
                        need_open_comment,
                        only_fans_can_comment: need_open_comment
                          ? settings.only_fans_can_comment
                          : false,
                      })
                    }
                  />
                </div>
                <div className={ROW + " justify-between"}>
                  <span className="min-w-0 pr-4">
                    <span className="block text-[13px] font-medium">仅粉丝可评论</span>
                    <span className="block text-[12px] text-muted">开启评论后，限制为已关注用户。</span>
                  </span>
                  <Toggle
                    checked={settings.only_fans_can_comment}
                    disabled={settingsSaving || !settings.need_open_comment}
                    title="仅粉丝可评论"
                    onChange={(only_fans_can_comment) =>
                      void saveSettings({
                        need_open_comment: true,
                        only_fans_can_comment,
                      })
                    }
                  />
                </div>
              </>
            ) : (
              <div className={ROW + " justify-between text-[12.5px] text-muted"}>
                <span>评论设置暂时不可用。</span>
                <button className="text-accent" type="button" onClick={() => void retrySettings()}>
                  重试
                </button>
              </div>
            )}
          </div>
          <div className={FOOT + " flex items-center justify-between gap-3"} aria-live="polite">
            <span className={settingsError ? "text-danger" : ""} data-testid="wechat-settings-error">
              {settingsError || "设置会用于之后保存的公众号草稿。"}
            </span>
            {settingsSaving && <span className="shrink-0">正在保存…</span>}
          </div>
        </>
      )}
    </div>
  );
}
