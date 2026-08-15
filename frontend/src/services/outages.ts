import api from "./api";
import type { OutageCreatePayload, OutageReport, OutageStatus } from "../types";

export async function fetchOutages(status?: OutageStatus | null): Promise<OutageReport[]> {
  const { data } = await api.get<OutageReport[]>("/outages", {
    params: status ? { status } : undefined,
  });
  return data;
}

export async function createOutage(payload: OutageCreatePayload): Promise<OutageReport> {
  const { data } = await api.post<OutageReport>("/outages", payload);
  return data;
}

export async function updateOutageStatus(
  outageId: string,
  status: OutageStatus
): Promise<OutageReport> {
  const { data } = await api.patch<OutageReport>(`/outages/${outageId}/status`, { status });
  return data;
}
