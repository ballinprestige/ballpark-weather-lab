import type { GeometryArtifact, VenueGeometry } from './types';

const teamAliases: Record<string, string> = {
  'arizona diamondbacks': 'ARI', diamondbacks: 'ARI', ari: 'ARI',
  'atlanta braves': 'ATL', braves: 'ATL', atl: 'ATL',
  'baltimore orioles': 'BAL', orioles: 'BAL', bal: 'BAL',
  'boston red sox': 'BOS', 'red sox': 'BOS', bos: 'BOS',
  'chicago cubs': 'CHC', cubs: 'CHC', chc: 'CHC',
  'cincinnati reds': 'CIN', reds: 'CIN', cin: 'CIN',
  'cleveland guardians': 'CLE', guardians: 'CLE', cle: 'CLE',
  'colorado rockies': 'COL', rockies: 'COL', col: 'COL',
  'chicago white sox': 'CWS', 'white sox': 'CWS', cws: 'CWS',
  'detroit tigers': 'DET', tigers: 'DET', det: 'DET',
  'houston astros': 'HOU', astros: 'HOU', hou: 'HOU',
  'kansas city royals': 'KC', royals: 'KC', kc: 'KC',
  'los angeles angels': 'LAA', angels: 'LAA', laa: 'LAA',
  'los angeles dodgers': 'LAD', dodgers: 'LAD', lad: 'LAD',
  'miami marlins': 'MIA', marlins: 'MIA', mia: 'MIA',
  'milwaukee brewers': 'MIL', brewers: 'MIL', mil: 'MIL',
  'minnesota twins': 'MIN', twins: 'MIN', min: 'MIN',
  'new york mets': 'NYM', mets: 'NYM', nym: 'NYM',
  'new york yankees': 'NYY', yankees: 'NYY', nyy: 'NYY',
  'athletics': 'OAK', 'oakland athletics': 'OAK', oak: 'OAK',
  'philadelphia phillies': 'PHI', phillies: 'PHI', phi: 'PHI',
  'pittsburgh pirates': 'PIT', pirates: 'PIT', pit: 'PIT',
  'san diego padres': 'SD', padres: 'SD', sd: 'SD',
  'seattle mariners': 'SEA', mariners: 'SEA', sea: 'SEA',
  'san francisco giants': 'SF', giants: 'SF', sf: 'SF',
  'st. louis cardinals': 'STL', 'st louis cardinals': 'STL', cardinals: 'STL', stl: 'STL',
  'tampa bay rays': 'TB', rays: 'TB', tb: 'TB',
  'texas rangers': 'TEX', rangers: 'TEX', tex: 'TEX',
  'toronto blue jays': 'TOR', 'blue jays': 'TOR', tor: 'TOR',
  'washington nationals': 'WSH', nationals: 'WSH', wsh: 'WSH'
};

export function findVenueGeometry(artifact: GeometryArtifact | null, homeTeam: string): VenueGeometry | null {
  if (!artifact) return null;
  const normalized = homeTeam.trim().toLowerCase();
  const key = teamAliases[normalized] ?? homeTeam.trim().toUpperCase();
  return artifact.venues[key] ?? null;
}

export function wallPath(angles: number[], distances: number[]): string {
  const points = angles.map((angle, index) => {
    const radians = angle * Math.PI / 180;
    const distance = distances[index] ?? 0;
    const x = 210 + Math.sin(radians) * distance * 0.55;
    const y = 264 - Math.cos(radians) * distance * 0.55;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return `M210,264 L${points.join(' L')} Z`;
}

export function normalizeDegrees(value: number): number {
  return ((value % 360) + 360) % 360;
}
