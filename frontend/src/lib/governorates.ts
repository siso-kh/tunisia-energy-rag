/**
 * The 24 governorates (wilayas) of Tunisia — canonical French names.
 *
 * Names and order mirror `_TUNISIA_COORDS` in `src/database/seed.py`, so
 * values submitted from the report form match the seed data, the TunisiaMap
 * node matching (`normalizeRegion`) and the backend storage exactly.
 */
export const TUNISIA_GOVERNORATES = [
  "Tunis",
  "Ariana",
  "Ben Arous",
  "Manouba",
  "Nabeul",
  "Zaghouan",
  "Bizerte",
  "Béja",
  "Jendouba",
  "Le Kef",
  "Siliana",
  "Sousse",
  "Monastir",
  "Mahdia",
  "Kairouan",
  "Kasserine",
  "Sidi Bouzid",
  "Sfax",
  "Gabès",
  "Médenine",
  "Tataouine",
  "Gafsa",
  "Tozeur",
  "Kébili",
] as const;

export type Governorate = (typeof TUNISIA_GOVERNORATES)[number];
