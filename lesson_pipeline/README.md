# 닥터윤 수업자료 자동화 — 1단계 Unit 원본 분할

## 확인된 경로와 데이터베이스
- 업로드 폴더: Google Drive 닥터윤 GPT 자료 / 수업자료생성
- 폴더 ID: 1_sdFfn1cCPuiJrJ3im0VwD9oZMgYBlqU
- 기존 Notion 수업자료 DB: 08a73018e136493ba7aeb606d43bb0ff
- 교재 DB2: 3a0fa4082f0480fd83c3f719fdf665a3

## 현재 구현
1. 새 PDF를 Google Drive 폴더에서 1시간 간격으로 조회
2. Unit별 시작 페이지가 명확한 원본 PDF만 안전하게 분할
3. Unit 머리말 반복을 같은 Unit으로 처리
4. 목차/부록 등 분할 제외 페이지는 별도 PDF로 원본 보관
5. 작업 결과를 원본 파일 ID와 체크섬별 폴더에 저장
6. 중복 업로드 감지 및 페이지 수 검증
7. 단원 경계가 불확실하거나 스캔 PDF일 경우 자동 중단

## 엄격한 한계
- ChatGPT '수업자료 프로젝트'의 프로젝트 프롬프트 전문을 직접 확인하지 못했습니다.
- 따라서 수업자료 PDF·교사용 Cue PDF를 임의 형식으로 만들지 않으며 Notion 등록도 하지 않습니다.
- 기존에 확정된 기본 순서는 핵심 어휘→심화 어휘→핵심 구문·문법→수업 설명→직독직해입니다.
- 교사용 Cue는 분홍(어휘), 노랑(구문/직독직해), 하늘(문법/구조), 교재 문제 정답 표시가 필수입니다.
- 이 원칙은 참고용 기록이며 프로젝트 프롬프트 원문을 대체하지 않습니다.
- GitHub Actions에서 Drive 읽기/쓰기를 하려면 인증 설정이 필요합니다. 연결된 ChatGPT Google Drive 계정과 GitHub Actions는 인증을 자동 공유하지 않습니다.

## 활성화 조건
1. Google 서비스 계정에 해당 Drive 폴더 Editor 권한 부여
2. GitHub Actions Secret GDRIVE_SERVICE_ACCOUNT_JSON에 서비스 계정 JSON 등록
3. GitHub 운영 브랜치에 워크플로 반영
4. 실제 전체 교재 PDF 하나로 경계·페이지·분할 검증
5. 프로젝트 프롬프트 원문 확보·비교 및 AI 생성 실행기 인증
6. 생성물 품질 검수와 Notion 수업자료/교재 Relation 등록 테스트

현재 브랜치는 1~4단계의 기술적 구현 코드이며, 5~6단계는 원문과 인증이 확인될 때까지 의도적으로 비활성화되어 있습니다.

## 시험
pip install pymupdf google-api-python-client google-auth
python -m unittest discover -s lesson_pipeline/tests -v

## 결과물 보관 규칙 (확정)
- **ZIP/RAR/7z 압축 파일 생성 및 업로드 금지.** 압축본을 기본 결과물로 사용하지 않는다.
- 교재 결과 폴더 안에 `Unit 01_문법 주제`처럼 Unit 번호와 학습 주제가 보이는 폴더를 생성한다.
- 각 Unit 폴더에는 `원본.pdf`, `수업자료.pdf`, `TEACHER_CUE.pdf`, 필요한 `개념백지_영작.pdf`, `교사용정답.pdf`를 **개별 PDF**로 저장한다.
- Unit 외 원본 페이지도 PDF로 보관하며 원본 전체 교재는 변경하지 않는다.
- Notion 수업자료 DB에서 결과 링크는 ZIP이 아닌 **수업자료.pdf 직접 링크**로 설정한다. 본문에는 Cue·원본·백지·정답 각각의 직접 링크를 제공한다.
- Drive에 파일별 저장 확인 후 Notion 관계형 연결/직접 링크를 검증해야 완료로 표시한다.
