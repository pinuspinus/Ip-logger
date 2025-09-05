from pydantic import BaseModel
from typing import Optional

class PaymentResponse(BaseModel):
    payment_id: str
    payment_status: str
    pay_address: Optional[str] = None
    price_amount: float
    price_currency: str
    pay_amount: Optional[float] = None
    pay_currency: Optional[str] = None
    order_id: Optional[str] = None
    order_description: Optional[str] = None
    ipn_callback_url: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    purchase_id: Optional[str] = None
    amount_received: Optional[float] = None
    payin_extra_id: Optional[str] = None
    network: Optional[str] = None
    is_fixed_rate: Optional[bool] = None
    is_fee_paid_by_user: Optional[bool] = None

class MinAmountResponse(BaseModel):
    currency_from: str
    currency_to: str
    min_amount: float
    fiat_equivalent: Optional[float] = None

class InvoiceResponse(BaseModel):
    id: int                 # iid
    status: str
    order_id: Optional[str] = None
    price_amount: float
    price_currency: str
    pay_currency: Optional[str] = None      # если фиксируешь монету заранее
    invoice_url: str
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None
    is_fixed_rate: Optional[bool] = None
    is_fee_paid_by_user: Optional[bool] = None

class InvoicePaymentResponse(BaseModel):
    payment_id: str
    payment_status: str
    pay_address: str
    pay_amount: float
    pay_currency: str
    payin_extra_id: Optional[str] = None
    purchase_id: Optional[str] = None
