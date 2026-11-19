"""One shared, explicitly illustrative rectangle; not Tower's geographic model."""
AIRPORT_ID = 'SKYNET'
NAME = 'Skynet International Airport'
LATITUDE, LONGITUDE = 40.6413, -73.7781
GEOMETRY = {'south': LATITUDE-0.01, 'north': LATITUDE+0.01,
            'west': LONGITUDE-0.01, 'east': LONGITUDE+0.01,
            'rule': 'Inclusive latitude/longitude rectangle, a demo geometry only.',
            'viewport': {'south': LATITUDE-0.018, 'north': LATITUDE+0.018,
                         'west': LONGITUDE-0.014, 'east': LONGITUDE+0.014}}


def classify(latitude, longitude):
    return 'inside' if (GEOMETRY['south'] <= latitude <= GEOMETRY['north'] and
                        GEOMETRY['west'] <= longitude <= GEOMETRY['east']) else 'outside'


def natural_key(value):
    import re
    return tuple((0, int(p), len(p)) if p.isdigit() else (1, p.casefold(), p)
                 for p in re.split(r'(\d+)', value))
