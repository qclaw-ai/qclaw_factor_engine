import jqdatasdk as jq
jq.auth("15652932320", "JQ123@QuantDay")
d = "2026-04-03"  # 与 config 里 end_date 一致
codes = jq.get_index_stocks("000905.XSHG", date=d)
print(type(codes), len(codes) if codes is not None else None, codes[:5] if codes else codes)