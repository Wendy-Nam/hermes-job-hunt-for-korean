# Job-search briefing add-on

Session-derived rules for the recurring Korean job briefing cron.

## Search families
- Sales/frontline: 기술영업, 테크니컬세일즈, 솔루션영업, 프리세일즈, sales engineer, solution consultant, customer success, BDR, SDR, AE, account executive, inside sales, field sales, channel sales, 영업, 영업기획, 영업관리, 영업지원, 세일즈오퍼레이션
- Ops/analysis/product: sales ops, business ops, revenue operations, business analyst, data analyst, strategy & operations, PMO, 사업개발, 사업운영, 데이터 분석, 비즈니스 분석, 서비스기획, 제품기획, 프로덕트 매니저, 운영기획
- Tech/data junior spillover: backend, frontend, full stack, data engineer, data scientist, QA, devops, security, AI/ML, machine learning, mobile, 백엔드, 프론트엔드, 데이터 엔지니어, 데이터 사이언티스트, QA, 데브옵스, 보안, 앱개발

## Filters
- Hard exclude if JD explicitly asks for **3+ years**.
- Prefer 신입/인턴/주니어/1~2년/무관/경력무관.
- If years are unclear, keep the item only with a note: `경력요건 확인 필요`.

## Output hygiene
- For Discord, use `<https://...>` only to suppress link previews.
- Avoid markdown links and avoid raw URLs adjacent to titles.
- If nothing passes the filter, emit nothing.
