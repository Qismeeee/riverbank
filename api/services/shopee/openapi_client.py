from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from .schemas import EscrowDetail, Order, ReturnCase, ShopInfo

LIVE_HOST = "https://partner.shopeemobile.com"
SANDBOX_HOST = "https://openplatform.sandbox.test-stable.shopee.sg"
MAX_ORDER_WINDOW_SECONDS = 15 * 24 * 3600
ORDER_FIELDS = "total_amount,buyer_user_id,item_list"


@dataclass(frozen=True)
class ShopeeCredentials:
    partner_id: int
    partner_key: str
    host: str = LIVE_HOST


@dataclass
class ShopToken:
    shop_id: int
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds


def sign(
    partner_key: str,
    partner_id: int,
    path: str,
    timestamp: int,
    access_token: str = "",
    shop_id: int | None = None,
) -> str:
    base = f"{partner_id}{path}{timestamp}{access_token}{shop_id if shop_id is not None else ''}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()


class ShopeeClient:
    def __init__(
        self,
        creds: ShopeeCredentials,
        http: httpx.Client | None = None,
        now=time.time,
    ):
        self.creds = creds
        self.http = http or httpx.Client(base_url=creds.host, timeout=30)
        self.now = now

    def auth_url(self, redirect: str) -> str:
        path = "/api/v2/shop/auth_partner"
        ts = int(self.now())
        q = {
            "partner_id": self.creds.partner_id,
            "timestamp": ts,
            "sign": sign(self.creds.partner_key, self.creds.partner_id, path, ts),
            "redirect": redirect,
        }
        return f"{self.creds.host}{path}?{urlencode(q)}"

    def exchange_code(self, code: str, shop_id: int) -> ShopToken:
        data = self._post("/api/v2/auth/token/get", {"code": code, "shop_id": shop_id})
        return self._token(shop_id, data)

    def refresh(self, token: ShopToken) -> ShopToken:
        data = self._post(
            "/api/v2/auth/access_token/get",
            {"refresh_token": token.refresh_token, "shop_id": token.shop_id},
        )
        return self._token(token.shop_id, data)

    def _token(self, shop_id: int, data: dict) -> ShopToken:
        return ShopToken(
            shop_id=shop_id,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=self.now() + int(data.get("expire_in", 4 * 3600)) - 60,
        )

    def get_shop_info(self, token: ShopToken) -> ShopInfo:
        d = self._get("/api/v2/shop/get_shop_info", token, {})
        return ShopInfo(
            shop_id=token.shop_id,
            shop_name=d.get("shop_name", ""),
            region=d.get("region", ""),
            status=d.get("status", ""),
            shop_created_time=None,
        )

    def get_orders(self, token: ShopToken, time_from: int, time_to: int) -> list[Order]:
        sns: list[str] = []
        start = time_from
        while start < time_to:
            end = min(start + MAX_ORDER_WINDOW_SECONDS, time_to)
            cursor = ""
            while True:
                d = self._get(
                    "/api/v2/order/get_order_list",
                    token,
                    {
                        "time_range_field": "create_time",
                        "time_from": start,
                        "time_to": end,
                        "page_size": 100,
                        "cursor": cursor,
                    },
                )
                sns += [o["order_sn"] for o in d.get("order_list", [])]
                if not d.get("more"):
                    break
                cursor = d.get("next_cursor", "")
            start = end
        sns = list(dict.fromkeys(sns))
        orders: list[Order] = []
        for i in range(0, len(sns), 50):
            d = self._get(
                "/api/v2/order/get_order_detail",
                token,
                {
                    "order_sn_list": ",".join(sns[i : i + 50]),
                    "response_optional_fields": ORDER_FIELDS,
                },
            )
            for o in d.get("order_list", []):
                orders.append(
                    Order(
                        order_sn=o["order_sn"],
                        create_time=int(o["create_time"]),
                        order_status=o["order_status"],
                        total_amount=int(o.get("total_amount", 0)),
                        buyer_user_id=o.get("buyer_user_id"),
                        item_count=len(o.get("item_list", []) or []) or 1,
                    )
                )
        return orders

    def get_escrow(self, token: ShopToken, order_sn: str) -> EscrowDetail:
        d = self._get("/api/v2/payment/get_escrow_detail", token, {"order_sn": order_sn})
        inc = d.get("order_income", {})
        return EscrowDetail(
            order_sn=order_sn,
            escrow_amount=int(inc.get("escrow_amount", 0)),
            buyer_total_amount=int(inc.get("buyer_total_amount", 0)),
            commission_fee=int(inc.get("commission_fee", 0)),
            service_fee=int(inc.get("service_fee", 0)),
            seller_transaction_fee=int(inc.get("seller_transaction_fee", 0)),
            escrow_release_time=inc.get("escrow_release_time"),
        )

    def get_returns(self, token: ShopToken, time_from: int, time_to: int) -> list[ReturnCase]:
        out: list[ReturnCase] = []
        page = 0
        while True:
            d = self._get(
                "/api/v2/returns/get_return_list",
                token,
                {
                    "page_no": page,
                    "page_size": 100,
                    "create_time_from": time_from,
                    "create_time_to": time_to,
                },
            )
            for r in d.get("return", []):
                out.append(
                    ReturnCase(
                        return_sn=r["return_sn"],
                        order_sn=r["order_sn"],
                        create_time=int(r["create_time"]),
                        status=r["status"],
                        refund_amount=int(r.get("refund_amount", 0)),
                    )
                )
            if not d.get("more"):
                return out
            page += 1

    def _common(self, path: str, token: ShopToken | None) -> dict:
        ts = int(self.now())
        q = {"partner_id": self.creds.partner_id, "timestamp": ts}
        if token:
            q["access_token"] = token.access_token
            q["shop_id"] = token.shop_id
            q["sign"] = sign(
                self.creds.partner_key,
                self.creds.partner_id,
                path,
                ts,
                token.access_token,
                token.shop_id,
            )
        else:
            q["sign"] = sign(self.creds.partner_key, self.creds.partner_id, path, ts)
        return q

    def _get(self, path: str, token: ShopToken, params: dict) -> dict:
        r = self.http.get(path, params={**self._common(path, token), **params})
        return self._unwrap(r)

    def _post(self, path: str, body: dict) -> dict:
        r = self.http.post(
            path,
            params=self._common(path, None),
            json={"partner_id": self.creds.partner_id, **body},
        )
        return self._unwrap(r)

    @staticmethod
    def _unwrap(r: httpx.Response) -> dict:
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise RuntimeError(f"shopee {data['error']}: {data.get('message', '')}")
        return data.get("response", data)
