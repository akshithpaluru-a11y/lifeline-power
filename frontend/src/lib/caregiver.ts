// The caregiver's own session lives apart from the owner's, so one laptop can demo both.
export interface CaregiverSession { household_id: string; token: string; caregiver_id: string; name: string }
const KEY = 'lifeline.caregiver'
export const getCaregiver = (): CaregiverSession | null => { try { return JSON.parse(localStorage.getItem(KEY) ?? 'null') } catch { return null } }
export const setCaregiver = (s: CaregiverSession | null) => { try { s ? localStorage.setItem(KEY, JSON.stringify(s)) : localStorage.removeItem(KEY) } catch { /* ignore */ } }
