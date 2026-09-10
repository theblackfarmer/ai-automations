import io, zipfile, urllib.request
from pathlib import Path
import pandas as pd

URL='https://github.com/s-k-28/nq-es-trader-5k-payout/raw/refs/heads/main/data/Dataset_NQ_1min_2022_2025.csv'
out=Path('nq_bar_sample.parquet')

# Stream the real public NQ 1-minute CSV and retain a manageable development window.
req=urllib.request.Request(URL,headers={'User-Agent':'Astra6/1.0'})
with urllib.request.urlopen(req,timeout=120) as r:
    raw=r.read()

df=pd.read_csv(io.BytesIO(raw))
print('ROWS',len(df))
print('COLUMNS',list(df.columns))
print('HEAD')
print(df.head(10).to_string(index=False))
print('TAIL')
print(df.tail(10).to_string(index=False))
print('DATE_RANGE',df.iloc[:,0].min(),df.iloc[:,0].max())
print('NULLS',df.isna().sum().to_dict())
# Save a compact verified artifact for retrieval.
df.head(100000).to_parquet(out,index=False)
print('SAVED',out, out.stat().st_size)
