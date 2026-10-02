"""Frozen ASTRA data contract: source stamps are interval opens."""
import numpy as np
import pandas as pd
PRICE_COLUMNS = ['open', 'high', 'low', 'close']

def validate_raw(raw):
    required = {'timestamp', *PRICE_COLUMNS, 'volume'}
    if required - set(raw):
        raise ValueError(f'missing columns: {sorted(required - set(raw))}')
    d = raw.copy()
    ts = pd.to_datetime(d.timestamp)
    if not isinstance(ts.dtype, pd.DatetimeTZDtype):
        raise ValueError('raw timestamps must be timezone-aware')
    d['timestamp'] = ts.dt.tz_convert('UTC')
    if d.timestamp.isna().any() or d.timestamp.duplicated().any():
        raise ValueError('missing or duplicate timestamps')
    if not d.timestamp.is_monotonic_increasing:
        raise ValueError('timestamps must be strictly chronological')
    if (d.timestamp != d.timestamp.dt.floor('min')).any():
        raise ValueError('timestamps must be minute aligned')
    for c in PRICE_COLUMNS + ['volume']:
        d[c] = pd.to_numeric(d[c], errors='raise')
        if not np.isfinite(d[c].to_numpy(dtype=float)).all():
            raise ValueError(f'nonfinite {c}')
    if (d[PRICE_COLUMNS] <= 0).any().any():
        raise ValueError('nonpositive price')
    if (d.volume <= 0).any() or (d.volume % 1 != 0).any():
        raise ValueError('volume must be positive integer')
    if ((d.high < d[['open', 'close', 'low']].max(axis=1)) |
            (d.low > d[['open', 'close', 'high']].min(axis=1))).any():
        raise ValueError('invalid OHLC geometry')
    if not np.isclose(d[PRICE_COLUMNS].to_numpy()*4,
                      np.round(d[PRICE_COLUMNS].to_numpy()*4), atol=1e-7, rtol=0).all():
        raise ValueError('off-tick price')
    expected = d.timestamp + pd.Timedelta(minutes=1)
    if 'known_time' in d and not pd.to_datetime(d.known_time).equals(expected):
        raise ValueError('raw availability must equal minute close')
    d['known_time'] = expected
    return d.reset_index(drop=True)

def load_csv(path, forward=False):
    d = pd.read_csv(path)
    d.columns = [str(c).strip().lower() for c in d.columns]
    d = d.rename(columns={'timestamp et': 'timestamp', 'datetime': 'timestamp'})
    if 'timestamp' not in d:
        raise ValueError('CSV has no timestamp column')
    if forward:
        if not d.timestamp.astype(str).str.contains(r'(?:Z|[+-]\d{2}:?\d{2})$', regex=True).all():
            raise ValueError('forward timestamps require explicit timezone offsets')
        d['timestamp'] = pd.to_datetime(d.timestamp, utc=True)
        d = d[d.timestamp >= pd.Timestamp('2026-03-05', tz='America/New_York').tz_convert('UTC')].copy()
    else:
        ts = pd.to_datetime(d.timestamp)
        if isinstance(ts.dtype, pd.DatetimeTZDtype):
            d['timestamp'] = ts.dt.tz_convert('UTC')
        else:
            d['timestamp'] = ts.dt.tz_localize('America/New_York', ambiguous='raise', nonexistent='raise').dt.tz_convert('UTC')
    return validate_raw(d[['timestamp', *PRICE_COLUMNS, 'volume']])

def resample(raw, minutes):
    """Only complete consecutive buckets; timestamps are UTC close availability."""
    if minutes not in (1, 2, 3, 5, 15, 60, 240):
        raise ValueError('unsupported frozen timeframe')
    d = raw.copy()
    if d.empty:
        return pd.DataFrame({c: pd.Series(dtype='datetime64[ns, UTC]' if c in
                            ('timestamp', 'known_time', 'start_time') else float)
                             for c in ['timestamp', 'known_time', 'start_time', *PRICE_COLUMNS, 'volume']})
    if minutes == 240:
        wall = d.timestamp.dt.tz_convert('America/New_York').dt.tz_localize(None)
        session_day = (wall - pd.Timedelta(hours=18)).dt.normalize()
        anchors = (session_day + pd.Timedelta(hours=18)).dt.tz_localize('America/New_York').dt.tz_convert('UTC')
        steps = ((d.timestamp - anchors).dt.total_seconds() // (minutes*60)).astype(int)
        start = anchors + pd.to_timedelta(steps*minutes, unit='min')
    else:
        start = d.timestamp.dt.floor(f'{minutes}min')
    d['bucket'] = start
    out = d.groupby('bucket', sort=True).agg(
        open=('open','first'), high=('high','max'), low=('low','min'), close=('close','last'),
        volume=('volume','sum'), count=('timestamp','size'), first=('timestamp','min'),
        last=('timestamp','max'), unique=('timestamp','nunique'))
    good = ((out['count'] == minutes) & (out['unique'] == minutes) &
            (out['first'] == out.index) &
            (out['last'] == out.index+pd.Timedelta(minutes=minutes-1)))
    out = out.loc[good, PRICE_COLUMNS+['volume']].reset_index().rename(columns={'bucket':'start_time'})
    out['timestamp'] = out.start_time+pd.Timedelta(minutes=minutes)
    out['known_time'] = out.timestamp
    return out[['timestamp','known_time','start_time',*PRICE_COLUMNS,'volume']]
