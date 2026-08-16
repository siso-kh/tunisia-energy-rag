import { FormEvent, useState } from "react";
import { Megaphone } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { TUNISIA_GOVERNORATES } from "../../lib/governorates";
import { createOutage } from "../../services/outages";
import type { Utility } from "../../types";
import Button from "../ui/Button";

interface Props {
  picked: { lat: number; lng: number } | null;
}

export default function ReportForm({ picked }: Props) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [utility, setUtility] = useState<Utility>("STEG");
  const [region, setRegion] = useState("");
  const [description, setDescription] = useState("");
  const [success, setSuccess] = useState(false);

  const mutation = useMutation({
    mutationFn: createOutage,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["outages"] });
      setSuccess(true);
      setDescription("");
      setRegion("");
      setTimeout(() => setSuccess(false), 4000);
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!picked || !region.trim()) return;
    mutation.mutate({
      utility,
      region: region.trim(),
      latitude: picked.lat,
      longitude: picked.lng,
      description: description.trim() || null,
    });
  };

  const fieldClass =
    "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary/50 focus:ring-2 focus:ring-primary/15";

  return (
    <form onSubmit={handleSubmit} className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-3 flex items-center gap-2">
        <span className="flex size-7 items-center justify-center rounded-lg bg-electric/10 text-electric">
          <Megaphone className="size-4" aria-hidden="true" />
        </span>
        <h3 className="text-sm font-semibold text-foreground">{t("map.reportTitle")}</h3>
      </div>

      <div className="space-y-3 text-sm">
        <div>
          <label className="mb-1 block text-xs text-muted-foreground">{t("map.utility")}</label>
          <select
            value={utility}
            onChange={(e) => setUtility(e.target.value as Utility)}
            className={fieldClass}
          >
            <option value="STEG">{t("map.steg")}</option>
            <option value="SONEDE">{t("map.sonede")}</option>
            <option value="OTHER">{t("map.other")}</option>
          </select>
        </div>

        <div>
          <label htmlFor="report-region" className="mb-1 block text-xs text-muted-foreground">
            {t("map.region")}
          </label>
          <select
            id="report-region"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            required
            className={fieldClass}
          >
            <option value="" disabled>
              {t("map.selectRegion")}
            </option>
            {TUNISIA_GOVERNORATES.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1 block text-xs text-muted-foreground">
            {t("map.description")}
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className={fieldClass}
          />
        </div>

        {picked ? (
          <p className="text-xs text-primary">
            📍 {picked.lat.toFixed(4)}, {picked.lng.toFixed(4)}
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">{t("map.clickMap")}</p>
        )}

        <Button
          type="submit"
          disabled={!picked || !region.trim() || mutation.isPending}
          className="w-full"
        >
          {mutation.isPending ? "…" : t("map.submit")}
        </Button>

        {success && (
          <p className="text-center text-xs text-green-600 dark:text-green-400">
            ✓ {t("map.submitted")}
          </p>
        )}
        {mutation.isError && (
          <p className="text-center text-xs text-destructive">{t("chat.error")}</p>
        )}
      </div>
    </form>
  );
}
