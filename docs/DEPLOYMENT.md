# ChannelShift 회원 작업실 배포

GitHub 배포 파일 게시와 실제 웹서비스 공개는 서로 다른 작업이다. 이 문서는 회원 작업실 자체의 실행 방법을 설명한다. 작업실에서 생성한 고객 앱을 자동 운영 배포한다는 뜻은 아니다.

## 실행 구성

Python 3.10 이상 환경에 검증한 ChannelShift wheel을 설치한다. 회원별 Codex 연결에는 프로그램의 호환성 검사를 통과하는 공식 Codex CLI가 PATH에 필요하다. 운영자의 Codex 로그인 파일을 회원에게 복사하지 않는다.

저장소는 운영 계정만 접근할 수 있는 별도 디렉터리로 지정한다. `CHANNELSHIFT_HOME` 아래 `members`에 계정·회원별 프로젝트·Codex 연결 상태가 저장된다. Linux 디렉터리 권한은 0700, 비밀 파일은 0600이다. 기존 서버에서 사용 중인 데이터 디렉터리를 비우거나 다른 회원 데이터로 덮어쓰지 않는다. 이전 시 서버를 중지한 상태에서 보호된 백업을 만들고, 기존 DB와 연결 파일을 함께 보존한다.

```sh
export CHANNELSHIFT_HOME=/absolute/path/on-confirmed-data-disk/channelshift-data
python -m channelshift.member_web --port 5189 --public-origin https://channelshift.net
```

실제 disk1의 마운트 경로는 서버에 접속해 확인한 뒤 위 경로에 적용한다. `--public-origin` 또는 `CHANNELSHIFT_PUBLIC_ORIGIN`은 고정 HTTPS origin을 지정한다. 서버는 계속 `127.0.0.1`에만 수신한다. 리버스 프록시가 공개 도메인의 Host/Origin을 보존해야 한다. 임의 Forwarded 헤더로 도메인을 바꾸지 않는다. 공개 모드는 Secure 세션 쿠키와 고정 이메일 인증 주소를 사용한다.

현재 Windows PC에서는 이미 저장한 전용 SMTP 설정을 사용하는 다음 런처를 사용할 수 있다.

```powershell
.\scripts\start-members.ps1 -Port 5189 -PublicOrigin https://channelshift.net
```

로컬 전용 실행은 `-PublicOrigin`을 생략한다. 실행 계정이 바뀌면 개인 SMTP 파일 ACL과 Windows DPAPI 자격증명도 확인해야 한다.

## 서버 비밀값

- 메일: `CHANNELSHIFT_SMTP_HOST`, `CHANNELSHIFT_SMTP_PORT`, `CHANNELSHIFT_SMTP_FROM`, `CHANNELSHIFT_SMTP_USER`, `CHANNELSHIFT_SMTP_PASSWORD_FILE`, `CHANNELSHIFT_SMTP_MODE`.
- 서버 검토 기능: `TYPESAFE_API_KEY` 또는 Windows의 기존 전용 DPAPI 저장소.
- 공개 자료 수집: `APIFY_TOKEN_FILE` 또는 기존 서버 비밀 환경변수.

비밀값은 서비스 관리자의 보호된 설정에서 주입한다. Git, 릴리스 ZIP, 사용자 프롬프트, 브라우저 설정에 넣지 않는다. 메일 설정이 없으면 가입을 허용하지 않는다. 프로그램 시작만으로 메일 전송·공유 서비스의 실제 호출이 검증된 것은 아니다.

## Cloudflare Tunnel

지정한 서버에서만 전용 터널을 실행하고 정확한 도메인만 연결한다. 다음은 실제 터널 ID와 자격증명 경로를 채워야 하는 예시다.

```yaml
tunnel: YOUR_TUNNEL_ID
credentials-file: /private/path/YOUR_TUNNEL_ID.json
ingress:
  - hostname: channelshift.net
    service: http://127.0.0.1:5189
  - service: http_status:404
```

```sh
cloudflared tunnel --config /private/path/channelshift.yml ingress validate
cloudflared tunnel --config /private/path/channelshift.yml ingress rule https://channelshift.net/login
cloudflared tunnel --config /private/path/channelshift.yml run
```

HTTP를 HTTPS로 리다이렉트하고, 터널과 앱은 서비스 관리자로 재시작을 관리한다. 이미 다른 앱을 제공하는 터널·DNS 레코드는 소유 대상과 기존 설정을 확인하지 않고 교체하지 않는다. 설정 형식은 [Cloudflare 공식 문서](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/)를 따른다.

## 공개 후 검수

1. HTTPS `/login`과 정적 자산이 정상 응답하는지 확인한다.
2. 익명 `/delivery`가 로그인 화면으로 이동하고 보호 API가 거절되는지 확인한다.
3. 다른 Host, 다른 Origin, CSRF 누락 요청이 거절되는지 확인한다.
4. 실제 회원 세션 쿠키가 Secure·HttpOnly·SameSite를 갖는지 확인한다.
5. 합성 계정으로 이메일 인증·회원 격리·로그아웃을 확인한다. 테스트 메일 발송은 승인된 수신자에게만 한다.
6. 본인 Codex 연결, 요구사항 저장, 단계 검수, 필수 정책 차단, 납품 ZIP을 확인한다.
7. 저장소·백업·재시작·롤백을 확인하고 공개 URL과 배포 버전을 기록한다.

대규모 공개 가입 운영에는 비밀번호 복구, 운영 메일 큐, 남용 방지와 접근 감사 등 [남은 운영 기능](MEMBER_AUTH.md)을 별도로 검수한다. 공개 HTTPS 설정만으로 이 기능이 구현되지는 않는다.
