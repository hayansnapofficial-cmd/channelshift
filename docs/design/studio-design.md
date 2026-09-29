# Studio design reference

Generated with the built-in Image Gen tool. Reference: studio-concept-v1.png.

The concept is a visual reference; all application text, controls, forms, and icons are native HTML/CSS/SVG. No concept pixels are used as the interface.

## Extracted tokens

- True white background #ffffff, charcoal text #20242a, muted text #667085, border #e5e7eb, action blue #2563eb.
- 64 px header, 220 px project rail, 48–56 px workspace gutter, 800–820 px readable column.
- Korean system sans font: 32 px title, 20 px section heading, 15–16 px content, 13–14 px controls, 12–13 px secondary text.
- Thin dividers, 5–6 px input/button corners, no dashboard card grid, no decorative assets, no fake progress.
- Native chevrons and thin 1.7 px stroke arrows; no icon containers.
- One active primary action per workflow state. Six linear groups: requirements, wireframe, data (ERD/API/database), backend, frontend, delivery.
- At 620 px and below, the project rail becomes a compact row above the horizontal step list; content remains one column.

## Intentional functional additions

Account connections live in an on-demand dialog. Required operational policies expand below the current task. Generated file text, edits, review reasons, checks, and traces use disclosures with the same typography and dividers. The empty-project state uses the same native form system. These are required workflow states, not extra primary navigation.

## Final generation prompt

Use case: ui-mockup. Create one complete polished desktop product workspace screenshot for ChannelShift, 1440 × 1000-ish landscape aspect. This is the entire first main app screen, not marketing or dashboard. Audience: Korean solo builder explains their desired website naturally, answers Codex clarifying questions, confirms the specification, then works through screen design, data, backend, frontend, delivery. A calm, highly professional editorial workspace: pure #FFFFFF background; charcoal #20242A text; restrained #2563EB blue on only the active step and one primary action. No gradients, no warm beige, no shadows, no bento cards, no fake metrics, no badges, no illustrations.

Layout: 64px quiet header spanning full width. Left wordmark code-native text "ChannelShift" bold charcoal, a fine separator then project name "예약 서비스". Far right small account control "내 계정" with simple chevron. Below header a 220px narrow left project rail with faint vertical divider; title "프로젝트"; small outlined "+ 새 프로젝트" button; a selected row "예약 서비스" with barely tinted blue background; another row "콘텐츠 사이트"; lots of whitespace. No icon clutter.
Main workspace starts right of rail. Horizontal six-step linear navigation at top, as plain text labels with thin connecting rules, active first underlined blue: "요구사항", "화면 설계", "데이터", "백엔드", "프론트", "검수·납품". No fake completion checks on future steps.
Main content is a restrained 760px reading column aligned left in remaining space with broad margins. H1 "요구사항 정리" about 30px, supporting line "만들고 싶은 것을 적고, 필요한 질문에 답하세요." gray 15px. Followed by a quiet collapsible source row "처음 요청" with a small chevron; expanded source text on a very light gray inset plain area: "고객이 날짜와 시간을 선택해 예약하고, 관리자가 예약을 확인하는 웹사이트를 만들고 싶어요." This source frame is the only large content framing; no rounded cards nested.
Below, open conversational question/answer form headed "조금 더 알려주세요" 20px, brief text "Codex가 다음 내용을 확인하고 있어요." 14px. Question 1 label "예약은 누구나 할 수 있나요?" semibold 16px. A multiline input about 86px high containing "회원가입 없이 이름과 연락처를 입력해서 예약할 수 있어요." plain subtle border, 6px radius. Question 2 label "관리자는 예약을 어떻게 처리하나요?" same styling. Second multiline input containing "관리자가 예약을 확인하고 승인하거나 취소할 수 있어요." Matching spacing. Under questions exactly one strong blue primary button aligned left "답변 저장하고 다시 정리" with small right-arrow icon. At button right or beneath very small neutral "내 Codex 연결로 정리합니다."
Below main action, quiet horizontal divider and collapsed disclosure row "운영·정책" left, "필수 7개 항목" middle subtle gray, simple right chevron right. Under it brief muted text "판매·서비스·SaaS 사이트에 필요한 정보를 함께 확인합니다." Do not show fake counts completed.
Whitespace at bottom. Typography crisp Korean sans, Pretendard/Noto Sans KR mood. Header chrome 13–14px, body15–16px, lineheight1.6. Thin #E5E7EB separators. Consistent 8px spacing scale. Professional senior-designed UI that can be faithfully implemented with native HTML/CSS text/buttons/form controls. Only visible provider name is Codex. Avoid all unrelated labels, navigation, metrics, widgets, English kicker labels, decorative assets. Render all supplied Korean text accurately and legibly.
