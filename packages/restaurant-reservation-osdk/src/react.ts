import { useFoundryLiteOsdkClient } from "@foundry-lite/sdk/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import {
  CancelCustomerReservation,
  DiningTable,
  PayReservationDeposit,
  QuoteAtomicReservationPolicy,
  Reservation,
  ReserveTableWithAtomicInventory,
  Restaurant,
} from "./generated";
import type {
  DiningTable as DiningTableObject,
  Reservation as ReservationObject,
  ReservationPolicyQuote,
  ReserveTableWithAtomicInventoryParams,
  Restaurant as RestaurantObject,
} from "./generated";

export const RESTAURANT_OBJECT_ID = "seed-restaurant-1";
export const TABLE_OBJECT_ID = "seed-restaurant-1";

export type BookingPhase = "schedule" | "details" | "policy" | "success";
export type CustomerDetails = {
  guestName: string;
  guestPhone: string;
  guestEmail: string;
  specialRequest: string;
};

type BookingRequest = {
  reservationId: string;
  targetVersion: number;
  params: ReserveTableWithAtomicInventoryParams;
};

export type CustomerReservationScreenModel = {
  restaurant: RestaurantObject | null;
  table: DiningTableObject | null;
  restaurantProperties: RestaurantObject["properties"] | Record<string, never>;
  tableProperties: DiningTableObject["properties"] | Record<string, never>;
  phase: BookingPhase;
  setPhase: Dispatch<SetStateAction<BookingPhase>>;
  date: string;
  setDate: Dispatch<SetStateAction<string>>;
  partySize: number;
  setPartySize: Dispatch<SetStateAction<number>>;
  preference: string;
  setPreference: Dispatch<SetStateAction<string>>;
  selectedTime: string | null;
  details: CustomerDetails;
  setDetails: Dispatch<SetStateAction<CustomerDetails>>;
  quote: ReservationPolicyQuote | null;
  isLoading: boolean;
  isQuoting: boolean;
  isBooking: boolean;
  hasAcceptedPolicy: boolean;
  setHasAcceptedPolicy: Dispatch<SetStateAction<boolean>>;
  error: string | null;
  setError: Dispatch<SetStateAction<string | null>>;
  confirmedReservation: ReservationObject | null;
  isManaging: boolean;
  depositPartyThreshold: number;
  slots: string[];
  canContinueDetails: boolean;
  resetQuote(): void;
  runQuote(time: string): Promise<ReservationPolicyQuote | null>;
  submitBooking(): Promise<void>;
  runReservationAction(action: "PayReservationDeposit" | "CancelCustomerReservation"): Promise<void>;
};

const EMPTY_DETAILS: CustomerDetails = {
  guestName: "",
  guestPhone: "",
  guestEmail: "",
  specialRequest: "",
};

export function customerReservationDate(daysFromNow: number): string {
  const date = new Date(Date.now() + daysFromNow * 86_400_000);
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function clockMinutes(value: string): number {
  const [hours = 0, minutes = 0] = value.split(":").map(Number);
  return hours * 60 + minutes;
}

function serviceSlots(properties: RestaurantObject["properties"] | Record<string, never>): string[] {
  const start = clockMinutes(properties.serviceStartLocal ?? "17:30");
  const end = clockMinutes(properties.lastSeatingLocal ?? "21:30");
  const interval = properties.slotIntervalMinutes ?? 30;
  const slots: string[] = [];
  for (let cursor = start; cursor <= end; cursor += interval) {
    const hours = String(Math.floor(cursor / 60)).padStart(2, "0");
    const minutes = String(cursor % 60).padStart(2, "0");
    slots.push(`${hours}:${minutes}`);
  }
  return slots;
}

function reservationId(date: string, time: string): string {
  const suffix = crypto.randomUUID().slice(0, 8).toUpperCase();
  return `RSV-WEB-${date.replaceAll("-", "")}-${time.replace(":", "")}-${suffix}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "예약 처리 중 알 수 없는 오류가 발생했습니다.";
}

export function useCustomerReservationScreen(): CustomerReservationScreenModel {
  const osdk = useFoundryLiteOsdkClient();
  const [restaurant, setRestaurant] = useState<RestaurantObject | null>(null);
  const [table, setTable] = useState<DiningTableObject | null>(null);
  const [phase, setPhase] = useState<BookingPhase>("schedule");
  const [date, setDate] = useState(() => customerReservationDate(3));
  const [partySize, setPartySize] = useState(2);
  const [preference, setPreference] = useState("WINDOW");
  const [selectedTime, setSelectedTime] = useState<string | null>(null);
  const [details, setDetails] = useState<CustomerDetails>(EMPTY_DETAILS);
  const [quote, setQuote] = useState<ReservationPolicyQuote | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isQuoting, setIsQuoting] = useState(false);
  const [isBooking, setIsBooking] = useState(false);
  const [hasAcceptedPolicy, setHasAcceptedPolicy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pinnedBooking, setPinnedBooking] = useState<BookingRequest | null>(null);
  const [confirmedReservation, setConfirmedReservation] = useState<ReservationObject | null>(null);
  const [isManaging, setIsManaging] = useState(false);

  useEffect(() => {
    let isCurrent = true;
    void Promise.all([
      osdk(Restaurant).fetchOne(RESTAURANT_OBJECT_ID),
      osdk(DiningTable).fetchOne(TABLE_OBJECT_ID),
    ]).then(([restaurantObject, tableObject]) => {
      if (!isCurrent) return;
      setRestaurant(restaurantObject);
      setTable(tableObject);
    }).catch((loadError: unknown) => {
      if (isCurrent) setError(errorMessage(loadError));
    }).finally(() => {
      if (isCurrent) setIsLoading(false);
    });
    return () => { isCurrent = false; };
  }, [osdk]);

  const restaurantProperties: RestaurantObject["properties"] | Record<string, never> = restaurant?.properties ?? {};
  const tableProperties: DiningTableObject["properties"] | Record<string, never> = table?.properties ?? {};
  const depositPartyThreshold = restaurantProperties.largePartyThreshold ?? 4;
  const slots = useMemo(() => serviceSlots(restaurantProperties), [restaurantProperties]);
  const resetQuote = useCallback(() => {
    setSelectedTime(null);
    setQuote(null);
    setPinnedBooking(null);
    setError(null);
  }, []);

  const executeQuote = useCallback(async (
    time: string,
    objectIds: { restaurant: string; table: string },
  ): Promise<ReservationPolicyQuote | null> => {
    if (details.guestPhone.replace(/\D/g, "").length < 9) {
      setError("좌석 확인을 위해 연락 가능한 휴대전화 번호를 입력해 주세요.");
      return null;
    }
    setSelectedTime(time);
    setQuote(null);
    setError(null);
    setIsQuoting(true);
    try {
      const result = await osdk(QuoteAtomicReservationPolicy).executeFunction({
        restaurant: objectIds.restaurant,
        table: objectIds.table,
        desiredAt: `${date}T${time}:00+09:00`,
        requestedAt: new Date().toISOString(),
        partySize,
        guestPhone: details.guestPhone,
        seatingPreference: preference,
      });
      setQuote(result.output);
      return result.output;
    } catch (quoteError) {
      setError(errorMessage(quoteError));
      return null;
    } finally {
      setIsQuoting(false);
    }
  }, [date, details.guestPhone, osdk, partySize, preference]);

  const runQuote = useCallback(async (time: string) => {
    if (!restaurant || !table) return null;
    return executeQuote(time, { restaurant: restaurant.objectId, table: table.objectId });
  }, [executeQuote, restaurant, table]);

  const buildBookingRequest = useCallback(async (): Promise<BookingRequest> => {
    if (!restaurant || !table || !selectedTime) throw new Error("방문 시간을 다시 선택해 주세요.");
    const [freshRestaurant, freshTable] = await Promise.all([
      osdk(Restaurant).fetchOne(RESTAURANT_OBJECT_ID),
      osdk(DiningTable).fetchOne(TABLE_OBJECT_ID),
    ]);
    setRestaurant(freshRestaurant);
    setTable(freshTable);
    const freshQuote = await executeQuote(selectedTime, {
      restaurant: freshRestaurant.objectId,
      table: freshTable.objectId,
    });
    if (!freshQuote?.isBookable) throw new Error("좌석 상태가 바뀌었습니다. 다른 시간을 선택해 주세요.");
    const nextReservationId = reservationId(date, selectedTime);
    return {
      reservationId: nextReservationId,
      targetVersion: freshRestaurant.objectVersion,
      params: {
        restaurant: freshRestaurant.objectId,
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
  }, [date, details, executeQuote, osdk, partySize, preference, restaurant, selectedTime, table]);

  const runReservationAction = useCallback(async (
    action: "PayReservationDeposit" | "CancelCustomerReservation",
  ) => {
    if (!confirmedReservation) return;
    setError(null);
    setIsManaging(true);
    try {
      const [freshReservation, freshTable] = await Promise.all([
        osdk(Reservation).fetchOne(confirmedReservation.objectId),
        osdk(DiningTable).fetchOne(TABLE_OBJECT_ID),
      ]);
      const operationId = `${action === "PayReservationDeposit" ? "PAY" : "CANCEL"}-${freshReservation.objectId}`;
      const requestedAt = new Date().toISOString();
      let runStatus: string;
      if (action === "PayReservationDeposit") {
        const run = await osdk(PayReservationDeposit).startAction({
          objectId: freshReservation.objectId,
          expectedObjectVersion: freshReservation.objectVersion,
          params: {
            reservation: freshReservation.objectId,
            table: freshTable.objectId,
            requestedAt,
            paymentAttemptId: operationId,
            paymentMethodRef: "pm_sandbox_visa_4242",
          },
        }, { idempotencyKey: `customer:${action}:${freshReservation.objectId}`, waitSeconds: 30 });
        runStatus = run.status;
      } else {
        const run = await osdk(CancelCustomerReservation).startAction({
          objectId: freshReservation.objectId,
          expectedObjectVersion: freshReservation.objectVersion,
          params: {
            reservation: freshReservation.objectId,
            table: freshTable.objectId,
            requestedAt,
            cancellationId: operationId,
            cancellationReason: "CUSTOMER_REQUEST",
          },
        }, { idempotencyKey: `customer:${action}:${freshReservation.objectId}`, waitSeconds: 30 });
        runStatus = run.status;
      }
      if (runStatus !== "succeeded") {
        throw new Error(
          action === "PayReservationDeposit"
            ? "보증금 승인이 완료되지 않았습니다."
            : "취소 처리가 완료되지 않았습니다.",
        );
      }
      setConfirmedReservation(await osdk(Reservation).fetchOne(freshReservation.objectId));
    } catch (manageError) {
      setError(errorMessage(manageError));
    } finally {
      setIsManaging(false);
    }
  }, [confirmedReservation, osdk]);

  const submitBooking = useCallback(async () => {
    if (!hasAcceptedPolicy) {
      setError("취소·보증금·지각 정책에 동의해 주세요.");
      return;
    }
    setError(null);
    setIsBooking(true);
    try {
      const request = pinnedBooking ?? (await buildBookingRequest());
      if (!pinnedBooking) setPinnedBooking(request);
      const run = await osdk(ReserveTableWithAtomicInventory).startAction({
        objectId: RESTAURANT_OBJECT_ID,
        expectedObjectVersion: request.targetVersion,
        params: request.params,
      }, { idempotencyKey: `customer-book:${request.reservationId}`, waitSeconds: 30 });
      if (run.status !== "succeeded") {
        throw new Error("예약이 확정되지 않았습니다. 같은 요청으로 다시 확인해 주세요.");
      }
      setConfirmedReservation(await osdk(Reservation).fetchOne(request.reservationId));
      setPhase("success");
    } catch (bookingError) {
      setError(errorMessage(bookingError));
    } finally {
      setIsBooking(false);
    }
  }, [buildBookingRequest, hasAcceptedPolicy, osdk, pinnedBooking]);

  const canContinueDetails = details.guestName.trim().length >= 2
    && details.guestEmail.includes("@")
    && details.guestPhone.replace(/\D/g, "").length >= 9;
  return {
    restaurant, table, restaurantProperties, tableProperties, phase, setPhase, date, setDate,
    partySize, setPartySize, preference, setPreference, selectedTime, details, setDetails, quote,
    isLoading, isQuoting, isBooking, hasAcceptedPolicy, setHasAcceptedPolicy, error, setError,
    confirmedReservation, isManaging, depositPartyThreshold, slots, canContinueDetails,
    resetQuote, runQuote, submitBooking, runReservationAction,
  };
}
