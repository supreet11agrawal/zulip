# Webhooks for external integrations.
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from django.http import HttpRequest, HttpResponse
from django.utils.translation import ugettext as _

from zerver.decorator import api_key_only_webhook_view
from zerver.lib.request import REQ, has_request_variables
from zerver.lib.response import json_error, json_success
from zerver.lib.webhooks.common import check_send_webhook_message, \
    UnexpectedWebhookEventType
from zerver.models import UserProfile

@api_key_only_webhook_view('Stripe')
@has_request_variables
def api_stripe_webhook(request: HttpRequest, user_profile: UserProfile,
                       payload: Dict[str, Any]=REQ(argument_type='body'),
                       stream: str=REQ(default='test')) -> HttpResponse:
    event_type = payload["type"]  # invoice.created, customer.subscription.created, etc
    if len(event_type.split('.')) == 3:
        category, resource, event = event_type.split('.')
    else:
        resource, event = event_type.split('.')
        category = resource

    object_ = payload["data"]["object"]  # The full, updated Stripe object

    # Set the topic to the customer_id when we can
    topic = ''
    customer_id = object_.get("customer", None)
    if customer_id is not None:
        # Running into the 60 character topic limit.
        # topic = '[{}](https://dashboard.stripe.com/customers/{})' % (customer_id, customer_id)
        topic = customer_id
    body = None

    def default_body() -> str:
        return '{resource} {verbed}'.format(
            resource=linkified_id(object_['id']), verbed=event.replace('_', ' '))

    if category == 'account':  # nocoverage
        if event == 'updated':
            topic = "account updates"
            body = ''
        else:
            # Part of Stripe Connect
            return json_success()
    if category == 'application_fee':  # nocoverage
        # Part of Stripe Connect
        return json_success()
    if category == 'balance':  # nocoverage
        # Not that interesting to most businesses, I think
        return json_success()
    if category == 'charge':
        if resource == 'charge':
            if not topic:  # only in legacy fixtures
                topic = 'charges'
            body = "{resource} for {amount} {verbed}".format(
                resource=linkified_id(object_['id']),
                amount=amount_string(object_['amount'], object_['currency']), verbed=event)
            if object_['failure_code']:  # nocoverage
                body += '. Failure code: {}'.format(object_['failure_code'])
        if resource == 'dispute':
            topic = 'disputes'
            body = default_body() + '. Current status: {status}.'.format(
                status=object_['status'].replace('_', ' '))
        if resource == 'refund':  # nocoverage
            topic = 'refunds'
            body = 'A {resource} for a {charge} of {amount} was updated.'.format(
                resource=linkified_id(object_['id'], lower=True),
                charge=linkified_id(object_['charge'], lower=True), amount=object_['amount'])
    if category == 'checkout_beta':  # nocoverage
        # Not sure what this is
        return json_success()
    if category == 'coupon':  # nocoverage
        # Not something that likely happens programmatically
        return json_success()
    if category == 'customer':
        if resource == 'customer':
            # Running into the 60 character topic limit.
            # topic = '[{}](https://dashboard.stripe.com/customers/{})' % (object_['id'], object_['id'])
            topic = object_['id']
            body = default_body()
            if event == 'created':
                if object_['email']:
                    body += '\nEmail: {}'.format(object_['email'])
                if object_['metadata']:  # nocoverage
                    for key, value in object_['metadata'].items():
                        body += '\n{}: {}'.format(key, value)
        if resource == 'discount':
            body = 'Discount {verbed} ([{coupon_name}]({coupon_url})).'.format(
                verbed=event.replace('_', ' '),
                coupon_name=object_['coupon']['name'],
                coupon_url='https://dashboard.stripe.com/{}/{}'.format('coupons', object_['coupon']['id'])
            )
        if resource == 'source':  # nocoverage
            body = default_body()
        if resource == 'subscription':
            body = default_body()
            if event == 'trial_will_end':
                DAY = 60 * 60 * 24  # seconds in a day
                # Basically always three: https://stripe.com/docs/api/python#event_types
                body += ' in {days} days'.format(
                    days=int((object_["trial_end"] - time.time() + DAY//2) // DAY))
            if event == 'created':
                if object_['plan']:
                    body += '\nPlan: [{plan_name}](https://dashboard.stripe.com/plans/{plan_id})'.format(
                        plan_name=object_['plan']['name'], plan_id=object_['plan']['id'])
                if object_['quantity']:
                    body += '\nQuantity: {}'.format(object_['quantity'])
                if 'billing' in object_:  # nocoverage
                    body += '\nBilling method: {}'.format(object_['billing'].replace('_', ' '))
    if category == 'file':  # nocoverage
        topic = 'files'
        body = default_body() + ' ({purpose}). \nTitle: {title}'.format(
            purpose=object_['purpose'].replace('_', ' '), title=object_['title'])
    if category == 'invoice':
        if event == 'upcoming':  # nocoverage
            body = 'Upcoming invoice created'
        else:
            body = default_body()
        if event == 'created':  # nocoverage
            # Could potentially add link to invoice PDF here
            body += ' ({reason})\nBilling method: {method}\nTotal: {total}\nAmount due: {due}'.format(
                reason=object_['billing_reason'].replace('_', ' '),
                method=object_['billing'].replace('_', ' '),
                total=amount_string(object_['total'], object_['currency']),
                due=amount_string(object_['amount_due'], object_['currency']))
    if category == 'invoiceitem':  # nocoverage
        body = default_body()
        if event == 'created':
            body += ' for {amount}'.format(amount=amount_string(object_['amount'], object_['currency']))
    if category.startswith('issuing'):  # nocoverage
        # Not implemented
        return json_success()
    if category.startswith('order'):  # nocoverage
        # Not implemented
        return json_success()
    if category in ['payment_intent', 'payout', 'plan', 'product', 'recipient',
                    'reporting', 'review', 'sigma', 'sku', 'source', 'subscription_schedule',
                    'topup', 'transfer']:  # nocoverage
        # Not implemented. In theory doing something like
        #   body = default_body()
        # may not be hard for some of these
        return json_success()

    if body is None:
        raise UnexpectedWebhookEventType('Stripe', event_type)

    if 'previous_attributes' in payload['data']:  # nocoverage
        previous_attributes = payload['data']['previous_attributes']
    else:
        previous_attributes = {}
    body += update_string(object_, previous_attributes)
    body = body.strip()

    check_send_webhook_message(request, user_profile, topic, body)
    return json_success()

def amount_string(amount: int, currency: str) -> str:
    zero_decimal_currencies = ["bif", "djf", "jpy", "krw", "pyg", "vnd", "xaf",
                               "xpf", "clp", "gnf", "kmf", "mga", "rwf", "vuv", "xof"]
    if currency in zero_decimal_currencies:
        decimal_amount = str(amount)  # nocoverage
    else:
        decimal_amount = '{0:.02f}'.format(float(amount) * 0.01)

    if currency == 'usd':  # nocoverage
        return '$' + decimal_amount
    return decimal_amount + ' {}'.format(currency.upper())

def update_string(object_: Dict[str, Any], previous_attributes: Dict[str, Any]) -> str:
    return ''.join('\n* ' + attribute.replace('_', ' ').capitalize() + ' is now ' + str(object_[attribute])
                   for attribute in previous_attributes)

def linkified_id(object_id: str, lower: bool=False) -> str:
    names_and_urls = {
        # Core resources
        'ch': ('Charge', 'charges'),
        'cus': ('Customer', 'customers'),
        'dp': ('Dispute', 'disputes'),
        'file': ('File', 'files'),
        'link': ('File link', 'file_links'),
        'pi': ('Payment intent', 'payment_intents'),
        'po': ('Payout', 'payouts'),
        'prod': ('Product', 'products'),
        're': ('Refund', 'refunds'),
        'tok': ('Token', 'tokens'),

        # Payment methods
        # payment methods have URL prefixes like /customers/cus_id/sources
        'ba': ('Bank account', None),
        'card': ('Card', None),
        'src': ('Source', None),

        # Billing
        # coupons have a configurable id, but the URL prefix is /coupons
        # discounts don't have a URL, I think
        'in': ('Invoice', 'invoices'),
        'ii': ('Invoice item', 'invoiceitems'),
        # products are covered in core resources
        # plans have a configurable id, though by default they are created with this pattern
        # 'plan': ('Plan', 'plans'),
        'sub': ('Subscription', 'subscriptions'),
        'si': ('Subscription item', 'subscription_items'),
        # I think usage records have URL prefixes like /subscription_items/si_id/usage_record_summaries
        'mbur': ('Usage record', None),

        # Undocumented :|
        'py': ('Payment', 'payments'),

        # Connect, Fraud, Orders, etc not implemented
    }  # type: Dict[str, Tuple[str, Optional[str]]]
    name, url_prefix = names_and_urls[object_id.split('_')[0]]
    if lower:  # nocoverage
        name = name.lower()
    if url_prefix is None:  # nocoverage
        return name
    return '[{}](https://dashboard.stripe.com/{}/{})'.format(name, url_prefix, object_id)
