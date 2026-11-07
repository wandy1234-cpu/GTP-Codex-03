# 数据源映射表（模板）

| source | market | field_in | field_out | required | note |
|---|---|---|---|---|---|
| akshare_spot | A/H | 代码 | ticker | Y | 标准化后入库 |
| akshare_spot | A/H | 名称 | name | N | 显示字段 |
| akshare_hist | A/H | 收盘 | close | Y | 复权口径需记录 |
| official_disclosure | A/H | 公告日期 | disclosure_date | N | as-of join 使用 |
