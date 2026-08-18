import { MapContainer, TileLayer, Marker, Popup, useMapEvents } from "react-leaflet";
import L from "leaflet";

/** Tunisia bounding box with a small buffer so the map can't be panned outside. */
const TUNISIA_BOUNDS = L.latLngBounds(
  [30.0, 7.3],   // south-west
  [37.8, 11.8]   // north-east
);
import { useTranslation } from "react-i18next";
import type { OutageReport, OutageStatus } from "../../types";
import "./leafletFix";

const STATUS_COLORS: Record<OutageStatus, string> = {
  PENDING: "#f59e0b",
  VERIFIED: "#ef4444",
  RESOLVED: "#22c55e",
};

function statusIcon(status: OutageStatus) {
  return L.divIcon({
    className: "",
    html: `<div style="width:18px;height:18px;border-radius:50%;background:${STATUS_COLORS[status]};border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,.4)"></div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}

/** Click handler that reports the clicked lat/lng (for the report form). */
function MapClickCatcher({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  useMapEvents({
    click(e) {
      onPick(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

interface Props {
  reports: OutageReport[];
  onPick?: (lat: number, lng: number) => void;
}

export default function OutageMap({ reports, onPick }: Props) {
  const { t } = useTranslation();

  return (
    <MapContainer
      center={[34.0, 9.5]}
      zoom={7}
      minZoom={6}
      maxBounds={TUNISIA_BOUNDS}
      maxBoundsViscosity={0.8}
      className="w-full h-full rounded-2xl"
      scrollWheelZoom
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {onPick && <MapClickCatcher onPick={onPick} />}
      {reports.map((report) => (
        <Marker
          key={report.id}
          position={[report.latitude, report.longitude]}
          icon={statusIcon(report.status)}
        >
          <Popup>
            <div className="text-sm">
              <div className="font-semibold">{report.region}</div>
              <div>{report.utility}</div>
              <div className="text-xs text-muted-foreground">{t(`map.${report.status.toLowerCase()}`)}</div>
              {report.description && (
                <p className="mt-1 text-xs">{report.description}</p>
              )}
            </div>
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}
