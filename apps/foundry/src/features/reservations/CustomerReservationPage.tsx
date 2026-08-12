import type { GenericObject } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
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
import { useEffect, useMemo, useState } from "react";

import "./customer-reservation.css";

const RESTAURANT_OBJECT_ID = "seed-restaurant-1";
const TABLE_OBJECT_ID = "seed-restaurant-1";
type BookingPhase = "schedule" | "details" | "policy" | "success";

type PolicyQuote = {
  decision: string;
  isBookable: boolean;
  reasonCodes: string[];
  depositAmountKrw: number;
  cancellationDeadlineAt: string;
  lateArrivalGraceMinutes: number;
  quoteExpiresAt: string;
  inventoryHoldStatus: string;
  holdDurationMinutes: number;
  serviceEndsAt: string;
  turnTimeMinutes: number;
  policyVersion: string;
  policyMessage: string;
};

type BookingRequest = {
  reservationId: string;
  targetVersion: number;
  params: Record<string, unknown>;
};

type CustomerDetails = {
  guestName: string;
  guestPhone: string;
  guestEmail: string;
  specialRequest: string;
};

const EMPTY_DETAILS: CustomerDetails = {
  guestName: "",
  guestPhone: "",
  guestEmail: "",
  specialRequest: "",
};

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" ? value : fallback;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function dateInputValue(daysFromNow: number): string {
  const date = new Date(Date.now() + daysFromNow * 86_400_000);
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function clockMinutes(value: string): number {
  const [hours, minutes] = value.split(":").map(Number);
  return hours * 60 + minutes;
}

function serviceSlots(properties: Record<string, unknown>): string[] {
  const start = clockMinutes(asString(properties.serviceStartLocal, "17:30"));
  const end = clockMinutes(asString(properties.lastSeatingLocal, "21:30"));
  const interval = asNumber(properties.slotIntervalMinutes, 30);
  const slots: string[] = [];
  for (let cursor = start; cursor <= end; cursor += interval) {
    const hours = String(Math.floor(cursor / 60)).padStart(2, "0");
    const minutes = String(cursor % 60).padStart(2, "0");
    slots.push(`${hours}:${minutes}`);
  }
  return slots;
}

function policyQuote(output: Record<string, unknown>): PolicyQuote {
  const raw = isRecord(output.value) ? output.value : output;
  return {
    decision: asString(raw.decision, "UNAVAILABLE"),
    isBookable: asBoolean(raw.isBookable),
    reasonCodes: Array.isArray(raw.reasonCodes)
      ? raw.reasonCodes.filter((item): item is string => typeof item === "string")
      : [],
    depositAmountKrw: asNumber(raw.depositAmountKrw, 0),
    cancellationDeadlineAt: asString(raw.cancellationDeadlineAt),
    lateArrivalGraceMinutes: asNumber(raw.lateArrivalGraceMinutes, 15),
    quoteExpiresAt: asString(raw.quoteExpiresAt),
    inventoryHoldStatus: asString(raw.inventoryHoldStatus, "AVAILABLE_NOT_HELD"),
    holdDurationMinutes: asNumber(raw.holdDurationMinutes, 10),
    serviceEndsAt: asString(raw.serviceEndsAt),
    turnTimeMinutes: asNumber(raw.turnTimeMinutes, 120),
    policyVersion: asString(raw.policyVersion, "unversioned"),
    policyMessage: asString(raw.policyMessage),
  };
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

function reservationId(date: string, time: string): string {
  const suffix = crypto.randomUUID().slice(0, 8).toUpperCase();
  return `RSV-WEB-${date.replaceAll("-", "")}-${time.replace(":", "")}-${suffix}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "예약 처리 중 알 수 없는 오류가 발생했습니다.";
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
  const client = useFoundryLiteClient();
  const [restaurant, setRestaurant] = useState<GenericObject | null>(null);
  const [table, setTable] = useState<GenericObject | null>(null);
  const [phase, setPhase] = useState<BookingPhase>("schedule");
  const [date, setDate] = useState(() => dateInputValue(3));
  const [partySize, setPartySize] = useState(2);
  const [preference, setPreference] = useState("WINDOW");
  const [selectedTime, setSelectedTime] = useState<string | null>(null);
  const [details, setDetails] = useState<CustomerDetails>(EMPTY_DETAILS);
  const [quote, setQuote] = useState<PolicyQuote | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isQuoting, setIsQuoting] = useState(false);
  const [isBooking, setIsBooking] = useState(false);
  const [hasAcceptedPolicy, setHasAcceptedPolicy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pinnedBooking, setPinnedBooking] = useState<BookingRequest | null>(null);
  const [confirmedReservation, setConfirmedReservation] = useState<GenericObject | null>(null);
  const [isManaging, setIsManaging] = useState(false);

  useEffect(() => {
    let isCurrent = true;
    void Promise.all([
      client.objects.generic.get("Restaurant", RESTAURANT_OBJECT_ID),
      client.objects.generic.get("DiningTable", TABLE_OBJECT_ID),
    ])
      .then(([restaurantObject, tableObject]) => {
        if (!isCurrent) return;
        setRestaurant(restaurantObject);
        setTable(tableObject);
      })
      .catch((loadError: unknown) => {
        if (isCurrent) setError(errorMessage(loadError));
      })
      .finally(() => {
        if (isCurrent) setIsLoading(false);
      });
    return () => {
      isCurrent = false;
    };
  }, [client]);

  const restaurantProperties = restaurant?.properties ?? {};
  const tableProperties = table?.properties ?? {};
  const depositPartyThreshold = asNumber(restaurantProperties.largePartyThreshold, 4);
  const slots = useMemo(() => serviceSlots(restaurantProperties), [restaurantProperties]);

  const resetQuote = () => {
    setSelectedTime(null);
    setQuote(null);
    setPinnedBooking(null);
    setError(null);
  };

  const runQuote = async (
    time: string,
    objectIds = { restaurant: restaurant?.objectId, table: table?.objectId },
  ) => {
    if (!objectIds.restaurant || !objectIds.table) return null;
    if (details.guestPhone.replace(/\D/g, "").length < 9) {
      setError("좌석 확인을 위해 연락 가능한 휴대전화 번호를 입력해 주세요.");
      return null;
    }
    setSelectedTime(time);
    setQuote(null);
    setError(null);
    setIsQuoting(true);
    try {
      const result = await client.functions.generic.execute("QuoteAtomicReservationPolicy", {
        inputs: {
          restaurant: objectIds.restaurant,
          table: objectIds.table,
          desiredAt: `${date}T${time}:00+09:00`,
          requestedAt: new Date().toISOString(),
          partySize,
          guestPhone: details.guestPhone,
          seatingPreference: preference,
        },
      });
      const nextQuote = policyQuote(result.output);
      setQuote(nextQuote);
      return nextQuote;
    } catch (quoteError) {
      setError(errorMessage(quoteError));
      return null;
    } finally {
      setIsQuoting(false);
    }
  };

  const buildBookingRequest = async (): Promise<BookingRequest> => {
    if (!restaurant || !table || !selectedTime) throw new Error("방문 시간을 다시 선택해 주세요.");
    const [freshRestaurant, freshTable] = await Promise.all([
      client.objects.generic.get("Restaurant", RESTAURANT_OBJECT_ID),
      client.objects.generic.get("DiningTable", TABLE_OBJECT_ID),
    ]);
    setRestaurant(freshRestaurant);
    setTable(freshTable);
    const freshQuote = await runQuote(selectedTime, {
      restaurant: freshRestaurant.objectId,
      table: freshTable.objectId,
    });
    if (!freshQuote?.isBookable) throw new Error("좌석 상태가 바뀌었습니다. 다른 시간을 선택해 주세요.");
    const nextReservationId = reservationId(date, selectedTime);
    return {
      reservationId: nextReservationId,
      targetVersion: freshRestaurant.objectVersion,
      params: {
        restaurant: restaurant.objectId,
        table: freshTable.objectId,
        reservationId: nextReservationId,
        guestName: details.guestName.trim(),
        guestPhone: details.guestPhone.trim(),
        guestEmail: details.guestEmail.trim(),
        partySize,
        desiredAt: `${date}T${selectedTime}:00+09:00`,
        requestedAt: new Date().toISOString(),
        seatingPreference: preference,
        specialRequest: details.specialRequest.trim(),
        customerAcceptedPolicy: true,
      },
    };
  };

  const runReservationAction = async (
    actionApiName: "PayReservationDeposit" | "CancelCustomerReservation",
  ) => {
    if (!confirmedReservation) return;
    setError(null);
    setIsManaging(true);
    try {
      const [freshReservation, freshTable] = await Promise.all([
        client.objects.generic.get("Reservation", confirmedReservation.objectId),
        client.objects.generic.get("DiningTable", TABLE_OBJECT_ID),
      ]);
      const operationId = `${actionApiName === "PayReservationDeposit" ? "PAY" : "CANCEL"}-${freshReservation.objectId}`;
      const requestedAt = new Date().toISOString();
      const shared = { reservation: freshReservation.objectId, table: freshTable.objectId, requestedAt };
      const params = actionApiName === "PayReservationDeposit"
        ? { ...shared, paymentAttemptId: operationId, paymentMethodRef: "pm_sandbox_visa_4242" }
        : { ...shared, cancellationId: operationId, cancellationReason: "CUSTOMER_REQUEST" };
      const run = await client.actions.runs.start(
        actionApiName,
        {
          target: { objectType: "Reservation", objectId: freshReservation.objectId },
          expectedObjectVersion: freshReservation.objectVersion,
          params,
        },
        {
          idempotencyKey: `customer:${actionApiName}:${freshReservation.objectId}`,
          waitSeconds: 30,
        },
      );
      if (run.status !== "succeeded") {
        throw new Error(actionApiName === "PayReservationDeposit" ? "보증금 승인이 완료되지 않았습니다." : "취소 처리가 완료되지 않았습니다.");
      }
      setConfirmedReservation(
        await client.objects.generic.get("Reservation", freshReservation.objectId),
      );
    } catch (manageError) {
      setError(errorMessage(manageError));
    } finally {
      setIsManaging(false);
    }
  };

  const submitBooking = async () => {
    if (!hasAcceptedPolicy) {
      setError("취소·보증금·지각 정책에 동의해 주세요.");
      return;
    }
    setError(null);
    setIsBooking(true);
    try {
      const request = pinnedBooking ?? (await buildBookingRequest());
      if (!pinnedBooking) setPinnedBooking(request);
      const run = await client.actions.runs.start(
        "ReserveTableWithAtomicInventory",
        {
          target: { objectType: "Restaurant", objectId: RESTAURANT_OBJECT_ID },
          expectedObjectVersion: request.targetVersion,
          params: request.params,
        },
        { idempotencyKey: `customer-book:${request.reservationId}`, waitSeconds: 30 },
      );
      if (run.status !== "succeeded") {
        throw new Error("예약이 확정되지 않았습니다. 같은 요청으로 다시 확인해 주세요.");
      }
      const created = await client.objects.generic.get("Reservation", request.reservationId);
      setConfirmedReservation(created);
      setPhase("success");
    } catch (bookingError) {
      setError(errorMessage(bookingError));
    } finally {
      setIsBooking(false);
    }
  };

  const canContinueDetails =
    details.guestName.trim().length >= 2 &&
    details.guestEmail.includes("@") &&
    details.guestPhone.replace(/\D/g, "").length >= 9;

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
              <label><span><CalendarDays aria-hidden="true" /> 방문일</span><input data-testid="booking-date" type="date" min={dateInputValue(0)} value={date} onChange={(event) => { setDate(event.target.value); resetQuote(); }} /></label>
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
