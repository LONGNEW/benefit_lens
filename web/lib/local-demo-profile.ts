import { createEmptyProfile, loadSavedProfile, loadWorkingProfile, normalizeProfile, saveProfile, saveWorkingProfile, WORKING_PROFILE_STORAGE_KEY, type PersonalProfile } from './browser-profile';

// A local demo link is device-specific. Its fragment never enters HTTP requests.
const minimalFields = ['institution', 'department', 'graduateSchool', 'academicLevel',
  'enrollmentStatus', 'graduateSemester', 'fullTimeStudent', 'highestDegreeLevel',
  'highestDegreeInstitution', 'birthYear', 'nationality', 'employmentStatus'] as const;
const prefix = '#local-profile=';

export function parseLocalDemoProfile(hostname: string, hash: string): Partial<PersonalProfile> | null {
  if (!['localhost', '127.0.0.1', '[::1]', '::1'].includes(hostname) || !hash.startsWith(prefix) || hash.length > 8000) return null;
  try {
    const encoded = hash.slice(prefix.length);
    if (!/^[A-Za-z0-9_-]+$/.test(encoded)) return null;
    const bytes = Uint8Array.from(atob(encoded.replaceAll('-', '+').replaceAll('_', '/')), char => char.charCodeAt(0));
    const envelope = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
    if (envelope?.version !== 1 || !envelope.profile || typeof envelope.profile !== 'object' || Array.isArray(envelope.profile)) return null;
    const normalized = normalizeProfile(envelope.profile);
    if (!normalized) return null;
    const patch: Partial<PersonalProfile> = {};
    for (const field of minimalFields) {
      const raw = envelope.profile[field];
      if (typeof raw !== 'string' || !raw.trim() || normalized[field] !== raw.trim()) continue;
      if (field === 'birthYear' && !/^(19|20)\d{2}$/.test(normalized[field])) continue;
      if (field === 'graduateSemester' && (!/^\d{1,2}$/.test(normalized[field]) || Number(normalized[field]) < 1 || Number(normalized[field]) > 30)) continue;
      patch[field] = normalized[field];
    }
    return Object.keys(patch).length ? patch : null;
  } catch { return null; }
}

export function applyLocalDemoProfile(current: PersonalProfile, patch: Partial<PersonalProfile>): PersonalProfile {
  const allowed = Object.fromEntries(minimalFields.filter(field => typeof patch[field] === 'string').map(field => [field, patch[field]]));
  // An old exact-age input must not override a newly supplied birth year.
  return {...current, ...allowed, ...(allowed.birthYear ? {age: ''} : {})};
}

type ProfileStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
type StorageAccess = { local: () => ProfileStorage; session: () => ProfileStorage };

export function initializeBrowserProfile(storage: StorageAccess, patch: Partial<PersonalProfile> | null = null) {
  let saved: PersonalProfile | null = null;
  let working: PersonalProfile | null = null;
  let localReadable = true;
  let sessionReadable = true;
  try { saved = loadSavedProfile(storage.local()); } catch { localReadable = false; }
  try { working = loadWorkingProfile(storage.session()); } catch { sessionReadable = false; }
  let profile = working ?? saved ?? createEmptyProfile();
  let message = !localReadable || !sessionReadable ? '브라우저 저장소 일부를 불러오지 못했습니다. 사용 가능한 입력만 복원했습니다.'
    : working ? '현재 탭에서 입력한 정보를 불러왔습니다.' : saved ? '저장한 기본정보를 불러왔습니다.' : '';
  if (patch) {
    profile = applyLocalDemoProfile(profile, patch);
    let savedDemo = false;
    let workingDemo = false;
    let staleSession = !sessionReadable;
    if (localReadable) {
      // Updating a demo must not erase previously saved extra facts after "clear input".
      const nextSaved = applyLocalDemoProfile(saved ?? profile, patch);
      try { saveProfile(storage.local(), nextSaved); saved = nextSaved; savedDemo = true; } catch { /* Keep the applied input even when persistence fails. */ }
    }
    if (sessionReadable) {
      try { saveWorkingProfile(storage.session(), profile); workingDemo = true; } catch {
        // Remove an obsolete tab snapshot only after its replacement is safely saved.
        if (savedDemo) {
          try { storage.session().removeItem(WORKING_PROFILE_STORAGE_KEY); } catch { staleSession = true; }
        }
      }
    }
    message = savedDemo ? '데모 기본정보를 적용하고 이 브라우저에 저장했습니다. 확인되지 않은 정보는 추가하지 않았습니다.'
      : workingDemo ? '데모 기본정보를 현재 탭에 적용했습니다. 다음 방문용 기본정보는 저장하지 못했습니다.'
        : '데모 기본정보를 적용했습니다. 저장소를 사용할 수 없어 이번 화면에서만 유지됩니다.';
    if (savedDemo && staleSession) message = '데모 기본정보는 저장했지만 현재 탭의 임시 저장소를 갱신하지 못했습니다. 새 탭에서 기본정보를 불러오세요.';
  }
  return { profile, savedProfile: saved, message };
}
