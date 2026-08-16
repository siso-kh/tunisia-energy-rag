import { geoMercator, geoPath, type GeoPermissibleObjects } from "d3-geo";

// Real (low-res) Tunisia border geometry, source: johan/world.geo.json (TUN)
const TUNISIA = {
  type: "Feature",
  properties: { name: "Tunisia" },
  geometry: {
    type: "Polygon",
    coordinates: [
      [
        [9.48214, 30.307556],
        [9.055603, 32.102692],
        [8.439103, 32.506285],
        [8.430473, 32.748337],
        [7.612642, 33.344115],
        [7.524482, 34.097376],
        [8.140981, 34.655146],
        [8.376368, 35.479876],
        [8.217824, 36.433177],
        [8.420964, 36.946427],
        [9.509994, 37.349994],
        [10.210002, 37.230002],
        [10.18065, 36.724038],
        [11.028867, 37.092103],
        [11.100026, 36.899996],
        [10.600005, 36.41],
        [10.593287, 35.947444],
        [10.939519, 35.698984],
        [10.807847, 34.833507],
        [10.149593, 34.330773],
        [10.339659, 33.785742],
        [10.856836, 33.76874],
        [11.108501, 33.293343],
        [11.488787, 33.136996],
        [11.432253, 32.368903],
        [10.94479, 32.081815],
        [10.636901, 31.761421],
        [9.950225, 31.37607],
        [10.056575, 30.961831],
        [9.970017, 30.539325],
        [9.48214, 30.307556],
      ],
    ],
  },
} as const;

const tunisiaFeature = TUNISIA as unknown as GeoPermissibleObjects;

export type Sector = "solar" | "electric" | "water" | "graphite";

type City = {
  name: string;
  coords: [number, number];
  sector: Sector;
  label: string;
};

const CITIES: City[] = [
  { name: "Tunis", coords: [10.1815, 36.8065], sector: "electric", label: "STEG · 1.8 GW" },
  { name: "Bizerte", coords: [9.8642, 37.2744], sector: "electric", label: "Grid Node" },
  { name: "Sousse", coords: [10.6412, 35.8256], sector: "solar", label: "PV · 320 MW" },
  { name: "Sfax", coords: [10.76, 34.7406], sector: "graphite", label: "ETAP Terminal" },
  { name: "Gabès", coords: [10.0982, 33.8815], sector: "water", label: "Desalination" },
  { name: "Tozeur", coords: [8.1335, 33.9197], sector: "solar", label: "Sahara PV" },
  { name: "Gafsa", coords: [8.7842, 34.425], sector: "solar", label: "PV · 210 MW" },
  { name: "Djerba", coords: [10.8451, 33.8076], sector: "water", label: "SONEDE" },
];

export const MAP_WIDTH = 520;
export const MAP_HEIGHT = 620;

const projection = geoMercator().fitExtent(
  [
    [24, 24],
    [MAP_WIDTH - 24, MAP_HEIGHT - 24],
  ],
  tunisiaFeature,
);

const pathGenerator = geoPath(projection);

export const tunisiaPath = pathGenerator(tunisiaFeature) ?? "";

export const cityNodes = CITIES.map((c) => {
  const point = projection(c.coords);
  return {
    name: c.name,
    sector: c.sector,
    label: c.label,
    x: point ? point[0] : 0,
    y: point ? point[1] : 0,
  };
});

/**
 * Project a real-world lat/lng onto the SVG canvas, so regions that are not
 * part of the curated `cityNodes` list (e.g. Nabeul, Kairouan) can still be
 * rendered as dynamic nodes from live outage data.
 */
export function projectPoint(
  lat: number,
  lng: number
): { x: number; y: number } | null {
  const point = projection([lng, lat]);
  if (!point) return null;
  const [x, y] = point;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x, y };
}
