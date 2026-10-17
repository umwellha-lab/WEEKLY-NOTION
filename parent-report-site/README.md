# 학부모 성과표 사이트 연결 코드

기존 dryoon 사이트에 적용할 변경 파일 모음입니다. 이 폴더만으로 독립 실행하거나 GitHub Pages에 배포할 수는 없습니다.
기존 사이트의 같은 경로에 파일을 반영합니다. 기존 DB 스키마, 개인 링크 검증 DB, UI 컴포넌트와 Cloudflare Workers 실행 환경을 사용합니다.

- 실행 설정과 제한: BIWEEKLY_SITE_CONNECTION.md
- 검증: Node 22.13 이상에서 이 폴더 안에서 `node --test tests/biweekly-report.test.mjs`
- 실제 학생 정보, 개인 링크, 노션 ID와 토큰은 포함하지 않습니다.
- 기존 Python 자동화와 사이트의 배포 설정은 변경하지 않습니다.
