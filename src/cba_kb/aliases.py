"""Season-aware club name resolution. Unknown names fail closed."""
import unicodedata
from pathlib import Path
import yaml

CONFIG = 'config/club_aliases.yaml'


class UnresolvedClub(ValueError):
    pass


def normalize(name):
    if name is None:
        raise UnresolvedClub('Empty club name')
    text = unicodedata.normalize('NFKC', str(name)).replace('\u3000', ' ')
    text = ' '.join(text.split())
    if not text:
        raise UnresolvedClub('Empty club name')
    return text


class Clubs:
    """Resolve a season club name to a stable club_id, or refuse to guess."""

    def __init__(self, root=None, path=None, strict=True):
        self.path = Path(path) if path else Path(root or '.') / CONFIG
        self.strict = strict
        self.unresolved = []
        self.data = yaml.safe_load(self.path.read_text())
        self.clubs = self.data['clubs']
        self._index = {}
        for club_id, item in self.clubs.items():
            for key in ('official_domestic', 'historical_domestic', 'media'):
                for name in item.get(key) or []:
                    self._index.setdefault((normalize(name), None), club_id)
            for season, names in (item.get('official_foreign_sponsor_by_season') or {}).items():
                for name in names or []:
                    self._index.setdefault((normalize(name), str(season)), club_id)
            for season, names in (item.get('source_typo_variant') or {}).items():
                for name in names or []:
                    self._index.setdefault((normalize(name), str(season)), club_id)

    def resolve(self, name, season=None, role='roster'):
        text = normalize(name)
        season = str(season) if season is not None else None
        club_id = self._index.get((text, season)) or self._index.get((text, None))
        if club_id is None:
            self.unresolved.append({'name': text, 'season': season})
            if self.strict:
                raise UnresolvedClub(f'Unknown club name {text!r} for season {season}')
            return None
        self.allowed(club_id, season, role)
        return club_id

    def allowed(self, club_id, season, role='roster'):
        item = self.clubs[club_id]
        if role != 'roster' or item.get('status') != 'defunct':
            return club_id
        last = item.get('last_roster_season')
        if item.get('post_dissolution_new_roster_forbidden') and last and season and str(season) > str(last):
            raise UnresolvedClub(f'{club_id} is defunct; no new roster after {last}')
        return club_id

    def report(self):
        unique = list({(item['name'], item['season']): item for item in self.unresolved}.values())
        return {'strict': self.strict, 'unresolved': unique,
                'known_clubs': len(self.clubs), 'aliases': len(self._index)}
