import {
  customerReservationDate,
  useCustomerReservationScreen,
} from "@foundry-lite/restaurant-reservation-osdk/react";
import type { BookingPhase } from "@foundry-lite/restaurant-reservation-osdk/react";
import {
  CalendarDays,
  Check,
  CheckCircle2,
  Clock3,
  CreditCard,
  MapPin,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  UtensilsCrossed,
  Users,
} from "lucide-react";
import "./customer-reservation.css";

const REASON_LABELS: Record<string, string> = {
  RESTAURANT_CLOSED: "현재 예약을 받지 않습니다.",
  TABLE_UNAVAILABLE: "이 좌석은 현재 예약할 수 없습니다.",
  TABLE_CAPACITY_EXCEEDED: "선택한 좌석의 최대 인원을 초과했습니다.",
  PARTY_TOO_SMALL: "선택한 좌석의 최소 인원보다 적습니다.",
  MINIMUM_NOTICE_NOT_MET: "방문 2시간 전까지 예약해 주세요.",
  OUTSIDE_BOOKING_WINDOW: "예약 가능한 30일 범위를 벗어났습니다.",
  CLOSED_WEEKDAY: "월요일은 정기 휴무입니다.",
  OUTSIDE_SERVICE_HOURS: "저녁 서비스 시간 밖의 요청입니다.",
  INVALID_SLOT_INTERVAL: "30분 단위의 시간을 선택해 주세요.",
  TURN_TIME_CONFLICT: "앞선 예약의 이용시간과 겹칩니다.",
  WINDOW_SEAT_UNAVAILABLE: "창가 좌석을 준비할 수 없습니다.",
  ACCESSIBLE_SEAT_UNAVAILABLE: "접근 가능한 좌석을 준비할 수 없습니다.",
};

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" ? value : fallback;
}

function formatKrw(value: number): string {
  return `${new Intl.NumberFormat("ko-KR").format(value)}원`;
}

function formatDateTime(value: string): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "long",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function StepRail({ phase }: { phase: BookingPhase }) {
  const current = { schedule: 1, details: 2, policy: 3, success: 4 }[phase];
  return (
    <ol className="booking-step-rail" aria-label="예약 진행 단계">
      {["일정", "정보", "정책", "완료"].map((label, index) => {
        const step = index + 1;
        return (
          <li key={label} data-state={step < current ? "done" : step === current ? "current" : "next"}>
            <span>{step < current ? <Check aria-hidden="true" /> : step}</span>
            <small>{label}</small>
          </li>
        );
      })}
    </ol>
  );
}

export default function CustomerReservationPage() {
  const {
    restaurantProperties, tableProperties, phase, setPhase, date, setDate, partySize,
    setPartySize, preference, setPreference, selectedTime, details, setDetails, quote,
    isLoading, isQuoting, isBooking, hasAcceptedPolicy, setHasAcceptedPolicy, error,
    setError, confirmedReservation, isManaging, depositPartyThreshold, slots,
    canContinueDetails, resetQuote, runQuote, submitBooking, runReservationAction,
  } = useCustomerReservationScreen();

  if (isLoading) {
    return (
      <div className="customer-booking-loading" data-testid="customer-reservation-page">
        <UtensilsCrossed aria-hidden="true" />
        <p>오늘의 테이블과 예약 정책을 불러오는 중입니다.</p>
      </div>
    );
  }

  return (
    <main className="customer-booking-page" data-testid="customer-reservation-page">
      <section className="restaurant-dossier" aria-label="식당 안내">
        <div className="restaurant-mark"><UtensilsCrossed aria-hidden="true" /> MCP</div>
        <div className="restaurant-intro">
          <p className="eyebrow">SEOUL · DINNER SERVICE</p>
          <h1>{asString(restaurantProperties.name, "MCP Bistro Seoul")}</h1>
          <p className="restaurant-description">
            제철 재료의 결을 살린 서울식 다이닝. 한 테이블의 리듬이 다음 손님까지
            이어지도록 예약마다 충분한 시간을 준비합니다.
          </p>
          <div className="restaurant-location">
            <MapPin aria-hidden="true" /> {asString(restaurantProperties.location, "서울 강남구")}
          </div>
        </div>

        <div className="service-note">
          <span>TONIGHT'S RHYTHM</span>
          <strong>{asString(restaurantProperties.serviceStartLocal, "17:30")} — {asString(restaurantProperties.lastSeatingLocal, "21:30")}</strong>
          <small>30분 간격 · 테이블당 {asNumber(tableProperties.turnTimeMinutes, 120)}분</small>
        </div>

        <div className="policy-dossier">
          <article>
            <Clock3 aria-hidden="true" />
            <div><strong>취소와 지각</strong><p>24시간 전까지 무료 취소 · 지각 유예 15분</p></div>
          </article>
          <article>
            <ShieldCheck aria-hidden="true" />
            <div><strong>{depositPartyThreshold}인 이상 보증금</strong><p>{depositPartyThreshold}인 이상 예약은 1인 {formatKrw(asNumber(restaurantProperties.depositPerPersonKrw, 20_000))}</p></div>
          </article>
          <article>
            <Sparkles aria-hidden="true" />
            <div><strong>창가 4인석</strong><p>휠체어 접근과 유아 의자 요청이 가능합니다.</p></div>
          </article>
        </div>
        <p className="policy-version">운영 정책 {asString(restaurantProperties.policyVersion, "2026.08")}</p>
      </section>

      <section className="booking-workspace" aria-label="예약하기">
        <header className="booking-header">
          <div><p className="eyebrow">RESERVE A TABLE</p><h2>저녁의 자리를 고르세요</h2></div>
          <StepRail phase={phase} />
        </header>

        {phase === "schedule" ? (
          <div className="booking-phase" data-testid="schedule-step">
            <div className="field-grid">
              <label><span><CalendarDays aria-hidden="true" /> 방문일</span><input data-testid="booking-date" type="date" min={customerReservationDate(0)} value={date} onChange={(event) => { setDate(event.target.value); resetQuote(); }} /></label>
              <label><span>연락처</span><input data-testid="guest-phone-schedule" inputMode="tel" autoComplete="tel" placeholder="010-0000-0000" value={details.guestPhone} onChange={(event) => { setDetails((current) => ({ ...current, guestPhone: event.target.value })); resetQuote(); }} /></label>
            </div>

            <fieldset className="choice-fieldset">
              <legend><Users aria-hidden="true" /> 방문 인원</legend>
              <div className="party-choices">
                {[2, 3, 4, 5, 6].map((size) => (
                  <button key={size} data-testid={`party-size-${size}`} type="button" aria-pressed={partySize === size} onClick={() => { setPartySize(size); resetQuote(); }}>
                    {size}명{size >= depositPartyThreshold ? <small>보증금</small> : null}
                  </button>
                ))}
              </div>
            </fieldset>

            <fieldset className="choice-fieldset compact">
              <legend>좌석 선호</legend>
              <div className="preference-choices">
                {[['WINDOW','창가'],['ACCESSIBLE','접근 가능한 좌석'],['ANY','어디든 좋아요']].map(([value, label]) => (
                  <button key={value} data-testid={`seat-${value.toLowerCase()}`} type="button" aria-pressed={preference === value} onClick={() => { setPreference(value); resetQuote(); }}>{label}</button>
                ))}
              </div>
            </fieldset>

            <div className="slot-section">
              <div className="slot-heading"><div><span className="eyebrow">SERVICE RHYTHM</span><h3>도착 시간</h3></div><p>시간을 누르면 현재 예약과 2시간 회전 정책을 함께 검사합니다.</p></div>
              <div className="service-timeline">
                {slots.map((time, index) => {
                  const isSelected = selectedTime === time;
                  const isLast = index === slots.length - 1;
                  return (
                    <button key={time} data-testid={`slot-${time.replace(":", "-")}`} type="button" className={isSelected ? "selected" : ""} onClick={() => void runQuote(time)}>
                      <span className="slot-time">{time}</span>
                      <span className="rhythm-line"><i />{isLast ? null : <b />}</span>
                      <span className="slot-copy"><strong>{index < 3 ? "이른 저녁" : index < 7 ? "메인 서비스" : "늦은 저녁"}</strong><small>{isSelected && isQuoting ? "정책 확인 중…" : isSelected ? "선택한 시간" : "좌석 정책 확인"}</small></span>
                    </button>
                  );
                })}
              </div>
            </div>

            {quote ? (
              <div className={`quote-card ${quote.isBookable ? "available" : "blocked"}`} data-testid="policy-quote">
                <div><strong>{quote.isBookable ? (quote.decision === "PAYMENT_REQUIRED" ? "보증금 결제 후 예약 가능" : "바로 확정 가능한 자리입니다") : "이 시간은 예약할 수 없습니다"}</strong><p>{quote.isBookable ? `${formatDateTime(`${date}T${selectedTime}:00+09:00`)} · ${partySize}명 · ${quote.turnTimeMinutes}분` : quote.reasonCodes.map((code) => REASON_LABELS[code] ?? code).join(" ")}</p></div>
                {quote.depositAmountKrw > 0 ? <span>{formatKrw(quote.depositAmountKrw)}</span> : <span>{quote.isBookable ? "보증금 없음" : "다른 시간 선택"}</span>}
                <small>견적 단계는 좌석을 선점하지 않습니다. 최종 제출은 서버 예약 원장과 예약을 한 번에 갱신합니다.</small>
              </div>
            ) : null}
            {error ? <p className="booking-error" role="alert">{error}</p> : null}
            <div className="phase-actions"><span /><button data-testid="continue-to-details" className="primary-action" type="button" disabled={!quote?.isBookable || isQuoting} onClick={() => setPhase("details")}>고객 정보 입력</button></div>
          </div>
        ) : null}

        {phase === "details" ? (
          <div className="booking-phase narrow" data-testid="details-step">
            <div className="phase-title"><span>02</span><div><h3>누구의 자리인가요?</h3><p>예약 변경과 식당 연락에 필요한 정보만 받습니다.</p></div></div>
            <label className="stacked-field"><span>예약자 이름</span><input data-testid="guest-name" autoComplete="name" value={details.guestName} onChange={(event) => setDetails((current) => ({ ...current, guestName: event.target.value }))} /></label>
            <label className="stacked-field"><span>이메일</span><input data-testid="guest-email" type="email" autoComplete="email" placeholder="name@example.com" value={details.guestEmail} onChange={(event) => setDetails((current) => ({ ...current, guestEmail: event.target.value }))} /></label>
            <label className="stacked-field"><span>휴대전화</span><input data-testid="guest-phone" autoComplete="tel" value={details.guestPhone} onChange={(event) => setDetails((current) => ({ ...current, guestPhone: event.target.value }))} /></label>
            <label className="stacked-field"><span>특별 요청 <small>선택</small></span><textarea data-testid="special-request" rows={4} placeholder="알레르기, 유아 의자, 기념일 등을 알려주세요." value={details.specialRequest} onChange={(event) => setDetails((current) => ({ ...current, specialRequest: event.target.value }))} /></label>
            <div className="phase-actions"><button className="text-action" type="button" onClick={() => setPhase("schedule")}>일정 다시 선택</button><button data-testid="continue-to-policy" className="primary-action" type="button" disabled={!canContinueDetails} onClick={() => { setError(null); setPhase("policy"); }}>정책 확인</button></div>
          </div>
        ) : null}

        {phase === "policy" && quote ? (
          <div className="booking-phase narrow" data-testid="policy-step">
            <div className="phase-title"><span>03</span><div><h3>마지막으로 확인해 주세요</h3><p>이 동의 내용과 정책 버전이 예약 객체에 함께 기록됩니다.</p></div></div>
            <div className="booking-summary">
              <dl><div><dt>방문</dt><dd>{formatDateTime(`${date}T${selectedTime}:00+09:00`)}</dd></div><div><dt>인원·좌석</dt><dd>{partySize}명 · {preference === "WINDOW" ? "창가" : preference === "ACCESSIBLE" ? "접근 가능한 좌석" : "좌석 무관"}</dd></div><div><dt>이용시간</dt><dd>{quote.turnTimeMinutes}분</dd></div><div><dt>보증금</dt><dd>{quote.depositAmountKrw > 0 ? formatKrw(quote.depositAmountKrw) : "없음"}</dd></div></dl>
            </div>
            <div className="policy-confirmation"><p><strong>취소</strong> {formatDateTime(quote.cancellationDeadlineAt)}까지 무료 취소</p><p><strong>지각</strong> 예약 시간부터 {quote.lateArrivalGraceMinutes}분까지 좌석 유지</p><p><strong>좌석</strong> 제출과 동시에 서버 원장을 잠그며, 보증금 예약은 {quote.holdDurationMinutes}분 동안 결제 자리를 유지</p></div>
            <label className="policy-check"><input data-testid="accept-policy" type="checkbox" checked={hasAcceptedPolicy} onChange={(event) => setHasAcceptedPolicy(event.target.checked)} /><span>위 취소·보증금·지각 정책과 개인정보 이용에 동의합니다. <small>정책 {quote.policyVersion}</small></span></label>
            {error ? <p className="booking-error" role="alert">{error}</p> : null}
            <div className="phase-actions"><button className="text-action" type="button" onClick={() => setPhase("details")}>정보 수정</button><button data-testid="submit-booking" className="primary-action" type="button" disabled={!hasAcceptedPolicy || isBooking} onClick={() => void submitBooking()}>{isBooking ? "정책과 좌석 재확인 중…" : quote.depositAmountKrw > 0 ? "보증금 조건으로 예약 요청" : "예약 확정"}</button></div>
          </div>
        ) : null}

        {phase === "success" && confirmedReservation ? (
          <div className="booking-success" data-testid="booking-success">
            <CheckCircle2 aria-hidden="true" />
            <p className="eyebrow">{asString(confirmedReservation.properties.status) === "CANCELLED" ? "RESERVATION CANCELLED" : asString(confirmedReservation.properties.paymentStatus) === "REQUIRED" ? "TABLE HELD · PAYMENT REQUIRED" : "RESERVATION CONFIRMED"}</p>
            <h3>{asString(confirmedReservation.properties.status) === "CANCELLED" ? "예약과 좌석 원장이 함께 취소됐습니다." : asString(confirmedReservation.properties.paymentStatus) === "REQUIRED" ? `${details.guestName}님의 자리를 잠시 잡아두었습니다.` : `${details.guestName}님의 저녁 자리를 준비할게요.`}</h3>
            <p>{formatDateTime(asString(confirmedReservation.properties.reservationAt))} · {asNumber(confirmedReservation.properties.partySize, partySize)}명</p>
            <div className="confirmation-ticket"><span>예약 번호</span><strong data-testid="reservation-id">{confirmedReservation.objectId}</strong><small>재고 {asString(confirmedReservation.properties.inventoryHoldStatus)} · 결제 {asString(confirmedReservation.properties.paymentStatus)}</small></div>
            {asString(confirmedReservation.properties.paymentStatus) === "REQUIRED" ? (
              <div className="payment-action-panel" data-testid="payment-panel">
                <div><CreditCard aria-hidden="true" /><p><strong>{formatKrw(asNumber(confirmedReservation.properties.quotedDepositKrw, 0))}</strong><small>{formatDateTime(asString(confirmedReservation.properties.holdExpiresAt))}까지 좌석 홀드</small></p></div>
                <button data-testid="pay-deposit" type="button" disabled={isManaging} onClick={() => void runReservationAction("PayReservationDeposit")}>{isManaging ? "승인 중…" : "테스트 카드로 보증금 승인"}</button>
                <small>LOCAL_SANDBOX 결제입니다. 상태 전이·재시도·환불은 실제 실행하지만 외부 PG의 실제 금전 이동은 없습니다.</small>
              </div>
            ) : null}
            {asString(confirmedReservation.properties.paymentStatus) === "AUTHORIZED" ? <p className="payment-boundary success" data-testid="payment-authorized">보증금 {formatKrw(asNumber(confirmedReservation.properties.paidDepositKrw, 0))} 승인 완료 · LOCAL_SANDBOX</p> : null}
            {asString(confirmedReservation.properties.status) === "CANCELLED" ? <p className="payment-boundary success" data-testid="refund-result">환불 상태 {asString(confirmedReservation.properties.refundStatus)} · {formatKrw(asNumber(confirmedReservation.properties.refundedAmountKrw, 0))}</p> : null}
            {asString(confirmedReservation.properties.status) !== "CANCELLED" && asString(confirmedReservation.properties.paymentStatus) !== "REQUIRED" ? <button data-testid="cancel-reservation" className="cancel-reservation-action" type="button" disabled={isManaging} onClick={() => void runReservationAction("CancelCustomerReservation")}><RotateCcw aria-hidden="true" />{isManaging ? "취소 정책 확인 중…" : "예약 취소 및 환불 확인"}</button> : null}
            {error ? <p className="booking-error" role="alert">{error}</p> : null}
            <div className="success-notes"><p><Clock3 aria-hidden="true" /> {asNumber(confirmedReservation.properties.lateArrivalGraceMinutes, 15)}분 지각 유예</p><p><ShieldCheck aria-hidden="true" /> {formatDateTime(asString(confirmedReservation.properties.cancellationDeadlineAt))}까지 무료 취소</p></div>
          </div>
        ) : null}
      </section>
    </main>
  );
}
