# 회원 서버 구성

`channelshift.net`의 회원 제작 작업실은 `disk1`의 별도 `channelshift/platform` 디렉터리에서 실행한다. 기존 DB 동기화 폴더 및 `www`의 다른 서비스와 분리한다. `compose.yaml`은 단일 회원 프로세스, 제한된 로컬 프록시, 전용 Cloudflare 터널을 관리한다.

## 빌드

1. 검증한 wheel과 이 Dockerfile을 비밀값이 없는 새 빌드 디렉터리에 놓는다.
2. 공식 Codex 0.159.0 Linux x86_64 musl **전체 패키지**를 받아 그 안의 파일을 `codex/` 아래 놓는다. 기존 서버의 Codex 설치는 교체하지 않는다.
3. 공식 패키지 SHA-256은 `35da65d7e8644e28ea0a4d4e3d8c15b40c6b492356d4cf21986c7e341f83a24e`이다. 받은 파일의 digest를 확인하고 링크·경로를 검사한 뒤 추출한다.
4. `docker build -t channelshift-members:0.2.3 .`로 이미지를 만든다.

Codex의 `--no-daemon`, 격리된 회원 홈, 도구 비활성화 옵션을 유지한다. 회원 본인의 로그인은 실제 접속 후 수행한다. 운영자의 로그인 파일을 전달하지 않는다.

## 운영 디렉터리

```text
platform/
  compose.yaml
  config/nginx.conf
  data/                         # 0700, UID 1000
  secrets/                      # 0700, 파일 0600, UID 1000
    runtime.env
    gmail-app-password.txt
    apify-token.txt
    cloudflare-tunnel-token.txt
  releases/                     # 비밀값이 없는 빌드 입력
```

비밀 파일은 저장소·이미지에 포함하지 않는다. 기존 저장소 이전은 실행 작업이 없는 상태에서 서버를 멈춘 뒤 SQLite backup API로 복사하고 무결성을 검사한다. 복사 후 원본은 보존한다. `runtime.env`에는 [배포 안내](../docs/DEPLOYMENT.md)의 메일·공용 검토 설정을 넣는다. 비밀번호 파일과 자료 수집 키는 컨테이너 안의 `/run/channelshift/` 경로를 사용한다.

앱과 프록시는 `127.0.0.1:5189`, `127.0.0.1:5191`만 사용한다. 터널의 정확한 호스트 `channelshift.net`을 `http://127.0.0.1:5191`로 연결한다. 프록시는 Host·Origin을 보존하고 요청량·본문 크기·연결 시간을 제한한다. Cloudflare가 제공하는 원본 HTTP scheme은 HTTPS 고정 주소로의 리다이렉트에만 사용하며 앱의 신뢰 origin 결정에 사용하지 않는다.

```sh
docker compose config --quiet
docker compose up -d members proxy
docker compose exec -T proxy nginx -t
curl -H 'Host: channelshift.net' http://127.0.0.1:5191/health
docker compose up -d tunnel
curl https://channelshift.net/health
```

## 복구와 검증

앱·프록시·터널은 `unless-stopped`로 재시작하며, 운영 DB·회원별 자료·메일·API 키는 이미지 밖에 남긴다. 이미지 교체 시 이전 이미지를 남겨 롤백할 수 있게 한다. 배포 전 비공개 DB 백업을 만들고 실행 중인 생성 작업이 없는지 확인한다. 데이터 스키마가 변경된 릴리스는 버전에 맞는 복구 절차를 추가해야 한다.

로그인 페이지 200, 익명 작업실의 로그인 이동, CSRF 및 회원 인증 거절, HTTP→HTTPS 이동, 회원 토큰으로 공용 기능 상태 조회를 검수한다. 상태 조회는 메일 발송이나 유료 공급자 호출 성공을 증명하지 않는다. 실제 생성·메일 발송은 사용자가 해당 작업을 요청할 때 수행한다.

회원 작업의 정본은 서버 저장소다. 이전 PC 저장소를 동시에 편집하면 자동으로 합쳐지지 않는다. MCP의 `CHANNELSHIFT_SERVICE_URL`에는 `https://channelshift.net`을 지정하고 기존 회원 키 파일을 유지한다. 이미 실행 중인 MCP는 설정 변경 후 다시 시작해야 한다.
