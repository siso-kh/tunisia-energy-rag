import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Save } from "lucide-react";
import Button from "../ui/Button";
import { fetchAdminConfig, updateAdminConfig } from "../../services/admin";

/** Known settings with their display + input metadata. */
const FIELDS: {
  key: string;
  labelKey: string;
  hintKey: string;
  type: "number";
  min?: number;
  step?: number;
}[] = [
  {
    key: "outage_ttl_hours",
    labelKey: "admin.config.ttl",
    hintKey: "admin.config.ttlHint",
    type: "number",
    min: 0.5,
    step: 0.5,
  },
  {
    key: "outage_purge_interval_minutes",
    labelKey: "admin.config.interval",
    hintKey: "admin.config.intervalHint",
    type: "number",
    min: 1,
    step: 1,
  },
  {
    key: "map_refresh_seconds",
    labelKey: "admin.config.mapRefresh",
    hintKey: "admin.config.mapRefreshHint",
    type: "number",
    min: 10,
    step: 10,
  },
];

export default function ConfigEditor() {
  const { t } = useTranslation();
  const [values, setValues] = useState<Record<string, string>>({});
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAdminConfig()
      .then((config) => {
        if (cancelled) return;
        setValues(config);
        setLoaded(true);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await updateAdminConfig(values);
      setValues(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (!loaded) {
    return (
      <div className="rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">
        {t("common.loading")}
      </div>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4 shadow-sm"
    >
      <div>
        <h3 className="text-sm font-semibold text-foreground">{t("admin.config.title")}</h3>
        <p className="text-xs text-muted-foreground">{t("admin.config.subtitle")}</p>
      </div>

      {FIELDS.map((field) => (
        <div key={field.key}>
          <label
            htmlFor={`cfg-${field.key}`}
            className="mb-1 block text-xs font-medium text-muted-foreground"
          >
            {t(field.labelKey)}
          </label>
          <input
            id={`cfg-${field.key}`}
            type={field.type}
            min={field.min}
            step={field.step}
            value={values[field.key] ?? ""}
            onChange={(e) =>
              setValues((prev) => ({ ...prev, [field.key]: e.target.value }))
            }
            required
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary/50 focus:ring-2 focus:ring-primary/15"
          />
          <p className="mt-0.5 text-[10px] text-muted-foreground">{t(field.hintKey)}</p>
        </div>
      ))}

      {error && <p className="text-xs text-destructive">{error}</p>}

      <div className="flex items-center gap-2">
        <Button type="submit" disabled={saving} className="flex-1">
          <Save className="size-4" aria-hidden="true" />
          {saving ? "…" : t("admin.config.save")}
        </Button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
            <Check className="size-3.5" aria-hidden="true" />
            {t("admin.config.saved")}
          </span>
        )}
      </div>
    </form>
  );
}
