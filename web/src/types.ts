// Mirrors the Pydantic models in api.py. Kept hand-written rather than
// generated: the surface is small, and a generator would be one more build
// step to keep working for no real gain at this size.

export interface Meeting {
  day: number;          // 0 = Monday, matching models.Day
  day_name: string;
  start: number;        // minutes since midnight, used for grid geometry
  end: number;
  start_label: string;  // formatted server-side; the browser never re-derives a time
  end_label: string;
}

export interface Section {
  section_id: string;   // the CRN a student registers with
  course_code: string;
  kind: string;
  section_no: string | null;
  instructor: string | null;
  rating: number | null;
  campus: string | null;
  meetings: Meeting[];
}

export interface Factor {
  name: string;
  label: string;
  score: number;
  weight_share: number;
  weighted_gap: number;
}

export interface Alternative {
  rank: number;
  reason: string;       // "best overall", "best for days on campus", ...
  factor: string | null;
  score: number;
  gap_from_ideal: number;
  variants: number;
  days_on_campus: number;
  sections: Section[];
  factors: Factor[];
  explanation: string | null;
}

export interface Course {
  code: string;
  title: string;
  sections: number;
  instructors: string[];
}

export interface GenerateResponse {
  generation_id: string;
  courses: Course[];
  missing: string[];
  campuses: string[];
  total_schedules: number;
  ratings: Record<string, number>;
}

export interface RankResponse {
  eligible: number;
  distinct_weeks: number;
  compromises: string[];
  alternatives: Alternative[];
}

export interface FactorMeta {
  name: string;
  label: string;
  default: number;
}

export interface Metadata {
  factors: FactorMeta[];
  terms: string[];
  days: string[];
}

export interface RankRequest {
  generation_id: string;
  preferred_days: string[];
  time_start: string;
  time_end: string;
  weights: Record<string, number>;
  required: Record<string, string>;
  commute_minutes: number;
  limit: number;
  narrate: number;
}
