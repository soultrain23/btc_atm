from __future__ import absolute_import
from time import sleep
import sys
import datetime
from datetime import datetime
from os.path import getmtime
import random
import requests
import atexit
import signal

from tiki_taka_bitmex.market_maker import bitmex
from tiki_taka_bitmex.market_maker.settings import settings
from tiki_taka_bitmex.market_maker.utils import log, constants, errors, math
from tiki_taka_bitmex.market_maker.utils import getCandleAvgMoveBitmex, PriceCalclater

# Used for reloading the bot - saves modified times of key files
import os
watched_files_mtimes = [(f, getmtime(f)) for f in settings.WATCHED_FILES]

import time
import threading
import winsound

#파싱
import urllib.request
import urllib.parse
import json

LEVERAGE = PriceCalclater.LEVERAGE[0]  # 레버리지
RECENT_PROFIT_TIME_GAP = 12 * 60 *60#12시간
COMMISION = PriceCalclater.COMMISSION
serial = settings.SERIAL

#현재 정보
floatBTCPrice = 1.0#가격
intBalancePosition = 0 #잔고 USB0
floatDelta = 0.0
LiqPrice = 0.0 #청산가
floatProfitRate = settings.floatProfitRate# 1%상승 했을 때 팔기
AVG_MOVING_RANGE = settings.AVG_MOVING_RANGE
LOSS_CUT_WAITING_TIME = settings.LOSS_CUT_WAITING_TIME
floatLossCutRate = settings.floatLossCutRate
BuySigned = 0 #매수 체결량
SellSigned = 0 #매도 체결량

#수익
floatBalanceXBT = 0.0#잔고 xbt
floatBalanceXBTFirst = 0.0#매수 전에
floatBalanceXBTSecond = 0.0#매도 후에

#구동에 필요
start_state = 0 #0구동, 1일시정지, 2즉시중지 ,3팔고중지
is_now_chking_profit = False #그날 익절률 계산중인지?
recent_profit_time = 0 #익절률 얻어오기
recent_process_time = 0 #작업 제대로 되고 있나요
is_thread_start = False
SYMBOL = settings.SYMBOL
RURNNING = 0
PAUSE = 1
STOP_NOW = 2
STOP_AFTER_SELL = 3

is_Long = False
#매매 예상
arrPosition = [0] * PriceCalclater.POSITION_CNT  # 매수 포지션 ㅇ
arrWeight = [0] * PriceCalclater.POSITION_CNT  # 매수 비중 ㅇ
arrWeightSum = [0] * PriceCalclater.POSITION_CNT  # 매수 비중 합, 들어간돈
arrWeightRate = [0] * PriceCalclater.POSITION_CNT  # 매수 비중 비율 ㅇ
arrAvgPosition = [0] * PriceCalclater.POSITION_CNT  # 평단가 ㅇ
arrProfitPosition = [0] * PriceCalclater.POSITION_CNT  # 이익률 가격
arrProfitRateReal = [0] * PriceCalclater.POSITION_CNT  # 실제 청산 상승률
arrProfit = [0] * PriceCalclater.POSITION_CNT  # 익절 대금
arrProfitRate = [0] * PriceCalclater.POSITION_CNT  # 익절률
arrMargincallPosition = [0] * PriceCalclater.POSITION_CNT  # 청산가격
arrMargincallFromNowRate = [0] * PriceCalclater.POSITION_CNT  # 매수가 대비 청산가 남은 하락률
arrMargincallOK = [False] * PriceCalclater.POSITION_CNT  # 청산가< 매수가
arrDecreaseRange = [0] * PriceCalclater.POSITION_CNT  # 청산가 하락률
arrMaxRate = PriceCalclater.arrMaxRate[0]

intPurchasePrice = 0 # 매수가
#intPurchasePriceCnt = 0 #몇번째까지 샀는지
intAvgPurchasePrice = 0 #평단
intContractPosition  = 0 #매수량, 매수 포지션
intLastOrderPrice = 0 #최근 주문가; 청산가 보다 높아야되서
intSellPrice = 0 #매도가
floatTotalProfit = 0 #이득
floatTotalProfitRate = 0 #손익률
intLowPriceRate = 0 #몇퍼까지 내려갔었는지
isNowTrade = False
isExitImmed = False
isExitNextTime = False
isMarginCallActivated = False
intNowBTCcnt = 0
strPurchaseTime = ""
#---------방어코드
#There will be no same price in 15 times that means error
intPriceSave = 0.0
intPriceSaveCnt = 0
PRICE_SAVE_CNT_LIMIT = settings.PRICE_SAVE_CNT_LIMIT
is_restart = False

#
# Helpers
#

logger = log.setup_custom_logger('root')


class ExchangeInterface:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        if len(sys.argv) > 1:
            self.symbol = sys.argv[1]
        else:
            self.symbol = settings.SYMBOL
        self.bitmex = bitmex.BitMEX(base_url=settings.BASE_URL, symbol=self.symbol,
                                    apiKey=settings.API_KEY, apiSecret=settings.API_SECRET,
                                    orderIDPrefix=settings.ORDERID_PREFIX, postOnly=settings.POST_ONLY,
                                    timeout=settings.TIMEOUT)

    def cancel_order(self, order):
        tickLog = self.get_instrument()['tickLog']
        logger.info("Canceling: %s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))
        while True:
            try:
                self.bitmex.cancel(order['orderID'])
                sleep(settings.API_REST_INTERVAL)
            except ValueError as e:
                logger.info(e)
                sleep(settings.API_ERROR_INTERVAL)
            else:
                break

    def cancel_all_orders(self):
        if self.dry_run:
            return

        logger.info("Resetting current position. Canceling all existing orders.")
        tickLog = self.get_instrument()['tickLog']
        # print(tickLog)
        # In certain cases, a WS update might not make it through before we call this.
        # For that reason, we grab via HTTP to ensure we grab them all.
        orders = self.bitmex.http_open_orders()
        # print(orders)
        for order in orders:
            logger.info("Canceling: %s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))

        if len(orders):
            self.bitmex.cancel([order['orderID'] for order in orders])

        sleep(settings.API_REST_INTERVAL)

    def get_portfolio(self):
        contracts = settings.CONTRACTS
        portfolio = {}
        for symbol in contracts:
            position = self.bitmex.position(symbol=symbol)
            instrument = self.bitmex.instrument(symbol=symbol)

            if instrument['isQuanto']:
                future_type = "Quanto"
            elif instrument['isInverse']:
                future_type = "Inverse"
            elif not instrument['isQuanto'] and not instrument['isInverse']:
                future_type = "Linear"
            else:
                raise NotImplementedError("Unknown future type; not quanto or inverse: %s" % instrument['symbol'])

            if instrument['underlyingToSettleMultiplier'] is None:
                multiplier = float(instrument['multiplier']) / float(instrument['quoteToSettleMultiplier'])
            else:
                multiplier = float(instrument['multiplier']) / float(instrument['underlyingToSettleMultiplier'])

            portfolio[symbol] = {
                "currentQty": float(position['currentQty']),
                "futureType": future_type,
                "multiplier": multiplier,
                "markPrice": float(instrument['markPrice']),
                "spot": float(instrument['indicativeSettlePrice'])
            }

        return portfolio

    def calc_delta(self):
        """Calculate currency delta for portfolio"""
        portfolio = self.get_portfolio()
        spot_delta = 0
        mark_delta = 0
        for symbol in portfolio:
            item = portfolio[symbol]
            if item['futureType'] == "Quanto":
                spot_delta += item['currentQty'] * item['multiplier'] * item['spot']
                mark_delta += item['currentQty'] * item['multiplier'] * item['markPrice']
            elif item['futureType'] == "Inverse":
                spot_delta += (item['multiplier'] / item['spot']) * item['currentQty']
                mark_delta += (item['multiplier'] / item['markPrice']) * item['currentQty']
            elif item['futureType'] == "Linear":
                spot_delta += item['multiplier'] * item['currentQty']
                mark_delta += item['multiplier'] * item['currentQty']
        basis_delta = mark_delta - spot_delta
        delta = {
            "spot": spot_delta,
            "mark_price": mark_delta,
            "basis": basis_delta
        }
        return delta

    def get_delta(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.get_position(symbol)['currentQty']

    def get_instrument(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.bitmex.instrument(symbol)

    def get_margin(self):
        if self.dry_run:
            return {'marginBalance': float(settings.DRY_BTC), 'availableFunds': float(settings.DRY_BTC)}
        return self.bitmex.funds()

    def get_orders(self):
        if self.dry_run:
            return []
        return self.bitmex.open_orders()

    def get_highest_buy(self):
        buys = [o for o in self.get_orders() if o['side'] == 'Buy']
        if not len(buys):
            return {'price': -2**32}
        highest_buy = max(buys or [], key=lambda o: o['price'])
        return highest_buy if highest_buy else {'price': -2**32}

    def get_lowest_sell(self):
        sells = [o for o in self.get_orders() if o['side'] == 'Sell']
        if not len(sells):
            return {'price': 2**32}
        lowest_sell = min(sells or [], key=lambda o: o['price'])
        return lowest_sell if lowest_sell else {'price': 2**32}  # ought to be enough for anyone

    def get_position(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.bitmex.position(symbol)

    def get_ticker(self, symbol=None):
        if symbol is None:
            symbol = self.symbol
        return self.bitmex.ticker_data(symbol)

    def is_open(self):
        """Check that websockets are still open."""
        return not self.bitmex.ws.exited

    def check_market_open(self):
        instrument = self.get_instrument()
        if instrument["state"] != "Open" and instrument["state"] != "Closed":
            raise errors.MarketClosedError("The instrument %s is not open. State: %s" %
                                           (self.symbol, instrument["state"]))

    def check_if_orderbook_empty(self):
        """This function checks whether the order book is empty"""
        instrument = self.get_instrument()
        if instrument['midPrice'] is None:
            raise errors.MarketEmptyError("Orderbook is empty, cannot quote")

    def amend_bulk_orders(self, orders):
        if self.dry_run:
            return orders
        return self.bitmex.amend_bulk_orders(orders)

    def create_bulk_orders(self, orders):
        if self.dry_run:
            return orders
        return self.bitmex.create_bulk_orders(orders)

    def cancel_bulk_orders(self, orders):
        if self.dry_run:
            return orders
        return self.bitmex.cancel([order['orderID'] for order in orders])

#######################################내가짠코딩###########################################
    def update_margin(self, syb, leverage):
        return self.bitmex.isolate_margin(syb, leverage)

class OrderManager:
    def __init__(self):
        self.exchange = ExchangeInterface(settings.DRY_RUN)
        # Once exchange is created, register exit handler that will always cancel orders
        # on any error.
        atexit.register(self.exit)
        signal.signal(signal.SIGTERM, self.exit)

        logger.info("Using symbol %s." % self.exchange.symbol)

        if settings.DRY_RUN:
            logger.info("Initializing dry run. Orders printed below represent what would be posted to BitMEX.")
        else:
            logger.info("Order Manager initializing, connecting to BitMEX. Live run: executing real trades.")

        self.start_time = datetime.now()
        self.instrument = self.exchange.get_instrument()
        self.starting_qty = self.exchange.get_delta()
        self.running_qty = self.starting_qty
        self.reset()

    def reset(self):
        self.exchange.cancel_all_orders()
        self.sanity_check()
        self.print_status()

        # Create orders and converge.
        # self.place_orders()

    def print_status(self):
        print('/////////////////////////', sys._getframe().f_code.co_name, '/////////////////////////')
        global floatBTCPrice, intBalancePosition, floatBalanceXBT, isNowTrade, intContractPosition, intAvgPurchasePrice, floatDelta, floatTotalProfit, floatTotalProfitRate, LiqPrice
        """Print the current MM status."""

        margin = self.exchange.get_margin()
        position = self.exchange.get_position()
        self.running_qty = self.exchange.get_delta()
        tickLog = self.exchange.get_instrument()['tickLog']
        self.start_XBt = margin["marginBalance"]
        floatDelta = abs(round(float(self.exchange.calc_delta()['spot']), 5))
        intBalancePosition = int((float(XBt_to_XBT(self.start_XBt)) * floatBTCPrice))
        floatBalanceXBT = abs(round(float(XBt_to_XBT(self.start_XBt)), 6))

        print('//////////////////////////////////////////////////////////////')
        print('////■■■//■//■//■//■///■■■///■////■//■/////■/////////')
        print('//////■////■//■■////■/////■///■/■///■■/////■/■///////')
        print('//////■////■//■//■//■/////■//■///■//■//■//■///■//////')
        print('//////////////////////////////////////////////////////////////')
        now = datetime.now()
        time_ = now.strftime('%Y-%m-%d %H:%M:%S')
        if is_Long:
            print(time_, 'Long 주문')
        else:
            print(time_, 'Short 주문')
        print('구매시간', strPurchaseTime[:16])
        print('XBT Balance 잔고', XBt_to_XBT(self.start_XBt))
        print('Contract Position 계약 $', self.running_qty, '')
        print('LEVERAGE ', LEVERAGE, '배')
        # logger.info("Current XBT Balance: %.6f" % XBt_to_XBT(self.start_XBt))
        # logger.info("Current Contract Position: %d" % self.running_qty)
        if (type(position['avgCostPrice']) is int or type(position['avgCostPrice']) is float) and position['avgCostPrice'] > 0:
            # logger.info("Position limits: %d/%d" % (settings.MIN_POSITION, settings.MAX_POSITION))
            isNowTrade = True
            intAvgPurchasePrice = round(float(position['avgCostPrice']), 2)
            intContractPosition = self.running_qty
            LiqPrice = float(position['liquidationPrice'])
            rate = 0

            if intSellPrice > 0:
                rate = round(abs(intAvgPurchasePrice - intSellPrice) / intAvgPurchasePrice * 100, 2)
            print('LiqPrice Price 청산가', float(LiqPrice))
            print('Avg Cost Price 평균단가', float(position['avgCostPrice']))
            print()
            print('floatBTCPrice 현재가', float(floatBTCPrice))
            if intSellPrice > 0:
                print('Avg', float(position['avgCostPrice']), '-> Sell', float(intSellPrice), '', float(rate), '% 수익 목표')
            if is_Long:
                now_rate = -round(((intAvgPurchasePrice - floatBTCPrice) / intAvgPurchasePrice) * 100 - COMMISION, 2)
            else:
                now_rate = round(((intAvgPurchasePrice - floatBTCPrice) / intAvgPurchasePrice) * 100 + COMMISION, 2)
            print('Now Margin 현수익률', float(now_rate), '%')
            # logger.info("LiqPrice Price: %d" % float(LiqPrice))
            # logger.info("Avg Cost Price: %.*f" % (tickLog, float(position['avgCostPrice'])))
            # logger.info("Avg Entry Price: %.*f" % (tickLog, float(position['avgEntryPrice'])))
            # logger.info("Avg: %d -> Sell : $d, %.2f per" % (float(LiqPrice), float(intSellPrice), float(rate)))
            # logger.info("Now Margin: %.2f per" % float(now_rate))
            # logger.info("LiqPrice Price: %d" % float(LiqPrice))
        else:
            isNowTrade = False
        print('Contracts Traded This Run', (self.running_qty - self.starting_qty))
        print('"Total Contract Delta 계약된 XBT', round(self.exchange.calc_delta()['spot'], 6))
        # logger.info("Contracts Traded This Run: %d" % (self.running_qty - self.starting_qty))
        # logger.info("Total Contract Delta: %.4f XBT" % self.exchange.calc_delta()['spot'])
        print('///////////////////////////////////////////////////////////')
        print('///////////////////////////////////////////////////////////')


        if floatTotalProfit != 0 and not is_sell and float(self.exchange.calc_delta()['spot']) == 0:
            self.sendProfit()  # 수익률 보내기
            sleep(60)

        floatTotalProfit = 0  # 이득

    def get_ticker(self):
        global start_position_buy, start_position_sell
        ticker = self.exchange.get_ticker()
        tickLog = self.exchange.get_instrument()['tickLog']

        # Set up our buy & sell positions as the smallest possible unit above and below the current spread
        # and we'll work out from there. That way we always have the best price but we don't kill wide
        # and potentially profitable spreads.
        start_position_buy = self.start_position_buy = ticker["buy"] + self.instrument['tickSize']
        start_position_sell = self.start_position_sell = ticker["sell"] - self.instrument['tickSize']

        # If we're maintaining spreads and we already have orders in place,
        # make sure they're not ours. If they are, we need to adjust, otherwise we'll
        # just work the orders inward until they collide.
        if settings.MAINTAIN_SPREADS:
            if ticker['buy'] == self.exchange.get_highest_buy()['price']:
                self.start_position_buy = ticker["buy"]
            if ticker['sell'] == self.exchange.get_lowest_sell()['price']:
                self.start_position_sell = ticker["sell"]

        # Back off if our spread is too small.
        if self.start_position_buy * (1.00 + settings.MIN_SPREAD) > self.start_position_sell:
            self.start_position_buy *= (1.00 - (settings.MIN_SPREAD / 2))
            self.start_position_sell *= (1.00 + (settings.MIN_SPREAD / 2))

        # Midpoint, used for simpler order placement.
        self.start_position_mid = ticker["mid"]
        # logger.info(
        #     "%s Ticker: Buy: %.*f, Sell: %.*f" %
        #     (self.instrument['symbol'], tickLog, ticker["buy"], tickLog, ticker["sell"])
        # )
        # logger.info('Start Positions: Buy: %.*f, Sell: %.*f, Mid: %.*f' %
        #             (tickLog, self.start_position_buy, tickLog, self.start_position_sell,
        #              tickLog, self.start_position_mid))
        return ticker

    ###
    # Position Limits
    ###
    def converge_orders(self, buy_orders, sell_orders):
        """Converge the orders we currently have in the book with what we want to be in the book.
           This involves amending any open orders and creating new ones if any have filled completely.
           We start from the closest orders outward."""

        tickLog = self.exchange.get_instrument()['tickLog']
        to_amend = []
        to_create = []
        to_cancel = []
        buys_matched = 0
        sells_matched = 0
        existing_orders = self.exchange.get_orders()

        # Check all existing orders and match them up with what we want to place.
        # If there's an open one, we might be able to amend it to fit what we want.
        naver_touch_order = 2 # 0-buy 1-sell 2-nothing
        if len(buy_orders) > 0 and type(buy_orders[0]) is int and buy_orders[0] == 1234567:
            buy_orders = []
            naver_touch_order = 0
        if len(sell_orders) > 0 and type(sell_orders[0]) is int and sell_orders[0] == 1234567:
            sell_orders = []
            naver_touch_order = 1

        for order in existing_orders:
            try:
                if order['side'] == 'Buy':
                    desired_order = buy_orders[buys_matched]
                    buys_matched += 1
                else:
                    desired_order = sell_orders[sells_matched]
                    sells_matched += 1

                # Found an existing order. Do we need to amend it?
                if desired_order['orderQty'] != order['leavesQty'] or (
                        # If price has changed, and the change is more than our RELIST_INTERVAL, amend.
                        desired_order['price'] != order['price'] and
                        abs((desired_order['price'] / order['price']) - 1) > settings.RELIST_INTERVAL):
                    to_amend.append({'orderID': order['orderID'], 'orderQty': order['cumQty'] + desired_order['orderQty'],
                                     'price': desired_order['price'], 'side': order['side']})
            except IndexError:
                # Will throw if there isn't a desired order to match. In that case, cancel it.
                if order['side'] == 'Buy' and naver_touch_order != 0:
                    to_cancel.append(order)
                if order['side'] == 'Sell' and naver_touch_order != 1:
                    to_cancel.append(order)


        while buys_matched < len(buy_orders):
            to_create.append(buy_orders[buys_matched])
            buys_matched += 1

        while sells_matched < len(sell_orders):
            to_create.append(sell_orders[sells_matched])
            sells_matched += 1

        if len(to_amend) > 0:
            for amended_order in reversed(to_amend):
                reference_order = [o for o in existing_orders if o['orderID'] == amended_order['orderID']][0]
                logger.info("Amending %4s: %d @ %.*f to %d @ %.*f (%+.*f)" % (
                    amended_order['side'],
                    reference_order['leavesQty'], tickLog, reference_order['price'],
                    (amended_order['orderQty'] - reference_order['cumQty']), tickLog, amended_order['price'],
                    tickLog, (amended_order['price'] - reference_order['price'])
                ))
            # This can fail if an order has closed in the time we were processing.
            # The API will send us `invalid ordStatus`, which means that the order's status (Filled/Canceled)
            # made it not amendable.
            # If that happens, we need to catch it and re-tick.
            try:
                self.exchange.amend_bulk_orders(to_amend)
            except requests.exceptions.HTTPError as e:
                errorObj = e.response.json()
                if errorObj['error']['message'] == 'Invalid ordStatus':
                    logger.warn("Amending failed. Waiting for order data to converge and retrying.")
                    sleep(0.5)
                    return self.run_loop()
                else:
                    logger.error("Unknown error on amend: %s. Exiting" % errorObj)
                    sys.exit(1)

        if len(to_create) > 0:
            logger.info("Creating %d orders:" % (len(to_create)))
            for order in reversed(to_create):
                logger.info("%4s %d @ %.*f" % (order['side'], order['orderQty'], tickLog, order['price']))
            self.exchange.create_bulk_orders(to_create)

        # Could happen if we exceed a delta limit
        if len(to_cancel) > 0:
            logger.info("Canceling %d orders:" % (len(to_cancel)))
            for order in reversed(to_cancel):
                logger.info("%4s %d @ %.*f" % (order['side'], order['leavesQty'], tickLog, order['price']))
            self.exchange.cancel_bulk_orders(to_cancel)
    def short_position_limit_exceeded(self):
        """Returns True if the short position limit is exceeded"""
        if not settings.CHECK_POSITION_LIMITS:
            return False
        position = self.exchange.get_delta()
        return position <= settings.MIN_POSITION

    def long_position_limit_exceeded(self):
        """Returns True if the long position limit is exceeded"""
        if not settings.CHECK_POSITION_LIMITS:
            return False
        position = self.exchange.get_delta()
        return position >= settings.MAX_POSITION

    ###
    # Sanity
    ##

    def sanity_check(self):
        print('/////////////////////////', sys._getframe().f_code.co_name, '/////////////////////////')
        global floatBTCPrice
        # Check if OB is empty - if so, can't quote.
        self.exchange.check_if_orderbook_empty()

        # Ensure market is still open.
        self.exchange.check_market_open()

        # Get ticker, which sets price offsets and prints some debugging info.
        ticker = self.get_ticker() #Price info

        floatBTCPrice = float(ticker["buy"])  # 현재가


    def check_file_change(self):
        """Restart if any files we're watching have changed."""
        for f, mtime in watched_files_mtimes:
            if getmtime(f) > mtime:
                self.restart()

    def check_connection(self):
        """Ensure the WS connections are still open."""
        return self.exchange.is_open()

    def exit(self):
        logger.info("Shutting down. All open orders will be cancelled.")
        try:
            # self.exchange.cancel_all_orders()
            self.exchange.bitmex.exit()
        except errors.AuthenticationError as e:
            logger.info("Was not authenticated; could not cancel orders.")
        except Exception as e:
            logger.info("Unable to cancel orders: %s" % e)
        self.restart()
        # sys.exit()


    #####################################내가 짠거 ######################################

    def get_order_success(self):
        # 매수 주문 성공할때까지 기다리기
        global intAvgPurchasePrice, floatBalanceXBTFirst, strPurchaseTime, is_Long, intContractPosition
        floatBalanceXBTFirst = floatBalanceXBT  # 매수 전 비트량
        # print('intContractPosition', intContractPosition)
        if intContractPosition < 0:
            is_Long = False
            print('Short Position')
        if intContractPosition > 0:
            is_Long = True
            print('Long Position')


        for a in range(10):
            sleep(7)
            position = self.exchange.get_position()
            self.running_qty = self.exchange.get_delta()
            intContractPosition = self.running_qty
            rate = intContractPosition / intBalancePosition *100
            if intContractPosition != 0 and rate > 90: #80이상은 사야지 뭘하지
                intAvgPurchasePrice = round(float(position['avgCostPrice']), 2)
                if intAvgPurchasePrice > 0:
                    break
            # print(intAvgPurchasePrice)
        now = datetime.now()
        strPurchaseTime = now.strftime('%Y-%m-%d %H:%M:%S')
        if intAvgPurchasePrice < 0:
            self.exchange.cancel_all_orders()  # 주문 취소


    # 매매 정보 보내기
    def sendTradeVal(self):
        print('/////////////////////////', sys._getframe().f_code.co_name, '/////////////////////////')
        global recent_process_time, SYMBOL
        recent_process_time = int(round(time.time()))#최근 구동 시간 정하기
        state = 2  # 0 수익률, 1 가격정보, 2 매매정보
        path = "http://btcatm.cafe24.com/bcfree9/in1.php?serial=" + serial

        userdata = {"state": state, "serial": serial, "c1": "time", "c2": SYMBOL, "c3": floatBTCPrice, "c4": float(floatBalanceXBT), "c5": floatDelta, "c6": intContractPosition, "c7":
            str(str(floatProfitRate) + ";" + str(recent_profit_time)), "c8": intAvgPurchasePrice, "c9": LiqPrice, "c10": intPurchasePrice, "c11": LEVERAGE, "c12":
                        RECENT_PROFIT_TIME_GAP, "c13": PRICE_SAVE_CNT_LIMIT, "c14": PriceCalclater.RANGE, "c15": PriceCalclater.POSITION_CNT, "c16": PriceCalclater.COMMISSION, "c17":
                        0, "c18": 0}
        resp = requests.post(path, params=userdata)
        #print('resp ', resp)

    # 수익률 보내기
    def sendProfit(self):
        print('/////////////////////////', sys._getframe().f_code.co_name, '/////////////////////////')

        state = 0  # 0 수익률, 1 가격정보, 2 매매정보
        path = "http://btcatm.cafe24.com/bcfree9/in1.php?serial=" + serial

        # 매도 후 비트량
        floatBalanceXBTSecond = float(floatBalanceXBT)
        profit = round((floatBalanceXBTSecond - floatBalanceXBTFirst) / floatBalanceXBTFirst * 100, 5)

        # 시간 최초가 매수대급합 매수비율
        delta = round(intContractPosition / floatBTCPrice, 5)
        purchase_sum_rate = round((delta / LEVERAGE) / floatBalanceXBT * 100, 2)
        # profit = floatProfitRate + COMMISION
        profitTotal = round((delta * profit / 100) / floatBalanceXBT * 100, 5)
        # print('settings.SYMBOL', settings.SYMBOL)
        userdata = {"state": state, "serial": serial, "c1": "time", "c2": intPurchasePrice, "c3": intContractPosition, "c4": round(intAvgPurchasePrice, 1), "c5": 0, "c6": profit, "c7": floatBalanceXBTSecond,
                    "c8": "0", "c9": "0", "c10": settings.SYMBOL}
        resp = requests.post(path, params=userdata)
    ###
    # Running
    ###
    def getMovingAvg(self):
        # Print('//////////////' + getframeinfo(currentframe()).function + '//////////////')
        global arrBTCPriceMovingAvg, arrBTCTimeMovingAvg, arrBTCCandleMovingAvg, arrBTCRangeMovingAvg
        for a in range(10):
            arrBTCPriceMovingAvg = []
            arrBTCTimeMovingAvg = []
            (arrBTCPriceMovingAvg, arrBTCTimeMovingAvg, arrBTCCandleMovingAvg, arrBTCRangeMovingAvg) = getCandleAvgMoveBitmex.getBTCPrice(AVG_MOVING_RANGE)
            print(arrBTCPriceMovingAvg)
            print(arrBTCTimeMovingAvg)
            if len(arrBTCPriceMovingAvg) > 0:
                break
            sleep(3)

    def getTimeSub(self, time1, time2):
        time1 = datetime(int(time1[:4]), int(time1[5:7]), int(time1[8:10]), int(time1[11:13]), int(time1[14:16]), int(time1[17:19]))
        time2 = datetime(int(time2[:4]), int(time2[5:7]), int(time2[8:10]), int(time2[11:13]), int(time2[14:16]), int(time2[17:19]))
        return ((time1 - time2).days * 86400 + (time1 - time2).seconds)

    def setLongShort(self):
        global is_Long
        now = datetime.now()
        time_ = now.strftime('%Y-%m-%d %H:%M:%S')
        idx = 0
        # for a in range(len(arrBTCTimeMovingAvg)):
            # print(time_, arrBTCTimeMovingAvg[a])
            # if self.getTimeSub(time_, arrBTCTimeMovingAvg[a]) < 0:
            #     # print(a, arrBTCTime[cnt], arrBTCTimeMovingAvg[a-1])
            #     # if arrBTCTime[cnt][:10] == arrBTCTimeMovingAvg[a][:10]:
            #     idx = a - 1
            #     if idx < 0:
            #         idx = 0
            #     break
                # exit()
        if intPurchasePrice < arrBTCPriceMovingAvg[len(arrBTCPriceMovingAvg) - 1]:  # 현재가 < 이평 -> 롱
            is_Long = True
            print('Now it is Long')
        else:
            is_Long = False
            print('Now it is Short')

        # is_Long = False

    def place_purchase_orders(self):
        print('/////////////////////////', sys._getframe().f_code.co_name, '/////////////////////////')
        global intLastOrderPrice, SellSigned, BuySigned

        if start_state == STOP_AFTER_SELL:
            raise print('STOP after sell')
        intLastOrderPrice = 0  # 마지막 주문가
        SellSigned = 0 # 매도 체결량
        if is_Long:
            buy_orders = []
            sell_orders = [1234567]  # 123456789면 취소하지 않기
            index = - 1
            intPurchasePrice = start_position_sell + 10
        else:
            buy_orders = [1234567]
            sell_orders = []  # 123456789면 취소하지 않기
            index = 1
            intPurchasePrice = start_position_sell - 10
            # Create orders from the outside in. This is intentional - let's say the inner order gets taken;
        # then we match orders from the outside in, ensuring the fewest number of orders are amended and only
        # a new order is created in the inside. If we did it inside-out, all orders would be amended
        # down and a new order would be created at the outside.
        weight_ = int(((intBalancePosition / floatBTCPrice) - floatDelta) * floatBTCPrice * LEVERAGE * 0.95)
        # print(intPurchasePrice, weight_, LEVERAGE)

        str = {'price': intPurchasePrice, 'orderQty': weight_, 'side': "Buy" if index < 0 else "Sell"}
        print('구매 주문', str)
        if is_Long:
            buy_orders.append(str)
        else:
            sell_orders.append(str)
        intLastOrderPrice = floatBTCPrice
        return self.converge_orders(buy_orders, sell_orders)

    def place_sell_orders(self, profit_rate):
        print('/////////////////////////', sys._getframe().f_code.co_name, '/////////////////////////')
        global intSellPrice, is_sell, floatTotalProfit, floatBalanceXBTFirst
        is_sell = False
        sleep(2)
        floatTotalProfit = intContractPosition
        if not is_Long:
            contract = BuySigned - SellSigned
            buy_orders = []
            sell_orders = [1234567]  # 123456789면 취소하지 않기
            index = - 1
        else:
            contract = -(BuySigned - SellSigned)
            buy_orders = [1234567]
            sell_orders = []  # 123456789면 취소하지 않기
            index = 1
        # print(intAvgPurchasePrice)
        # print(profit_rate)
        # print(intAvgPurchasePrice * (1 + (profit_rate + COMMISION) / 100), intAvgPurchasePrice, profit_rate)
        if is_Long:
            intSellPrice = PriceCalclater.getCeilNum(intAvgPurchasePrice * (1 + (profit_rate + COMMISION) / 100))
        else:
            intSellPrice = PriceCalclater.getFloorNum(intAvgPurchasePrice * (1 - (profit_rate + COMMISION) / 100))
        str = {'price': intSellPrice, 'orderQty': (intContractPosition), 'side': "Buy" if index < 0 else "Sell"}
        print('구매 주문', str)
        if abs(intContractPosition) > 0:
            if not is_Long:
                buy_orders.append(str)
            else:
                sell_orders.append(str)

            return self.converge_orders(buy_orders, sell_orders)

    def place_sell_orders_losscut(self):
        print('/////////////////////////', sys._getframe().f_code.co_name, '/////////////////////////')
        global intSellPrice, is_sell, floatTotalProfit, floatBalanceXBTFirst
        is_sell = False
        sleep(2)
        floatTotalProfit = intContractPosition
        is_loss_cut = False

        #손절률 밑으로 떨어졌는지
        if is_Long and floatBTCPrice > 0:
            profit_rate = round(((floatBTCPrice - intAvgPurchasePrice) / intAvgPurchasePrice * 100 - COMMISION), 2)
        elif not is_Long and floatBTCPrice > 0:
            profit_rate = round(-((floatBTCPrice - intAvgPurchasePrice) / intAvgPurchasePrice * 100 - COMMISION), 2)
        if profit_rate < -abs(floatLossCutRate):
            is_loss_cut = True
            print('넘 떨어져서 손절', profit_rate)

        #시간 됐는지
        if len(strPurchaseTime) > 5:
            now = datetime.now()
            nowTime = now.strftime('%Y-%m-%d %H:%M:%S')
            if self.getTimeSub(nowTime, strPurchaseTime) > settings.LOSS_CUT_WAITING_TIME:
                is_loss_cut = True
                print('시간되서 손절')

        if is_loss_cut:#손절하기
            self.exchange.cancel_all_orders()
            if not is_Long:
                buy_orders = []
                sell_orders = [1234567]  # 123456789면 취소하지 않기
                index = - 1
            else:
                buy_orders = [1234567]
                sell_orders = []  # 123456789면 취소하지 않기
                index = 1
            if is_Long:
                intSellPrice = int(floatBTCPrice * 0.95)
            else:
                intSellPrice = int(floatBTCPrice * 1.05)

            str = {'price': intSellPrice, 'orderQty': abs(intContractPosition), 'side': "Buy" if index < 0 else "Sell"}
            print('구매 주문', str)

            if abs(intContractPosition) > 0:
                if not is_Long:
                    buy_orders.append(str)
                else:
                    sell_orders.append(str)
                return self.converge_orders(buy_orders, sell_orders)
            if is_Long:
                intSellPrice = int(floatBTCPrice * 0.99)
            else:
                intSellPrice = int(floatBTCPrice * 1.01)

    def chkError(self):
        print('/////////////////////////', sys._getframe().f_code.co_name, '/////////////////////////')
        global intPriceSaveCnt, intPriceSave
        if intPriceSaveCnt > 5:
            print("Comparing same price: ", int(intPriceSaveCnt), " times.")
        # print "Connecting is ", self.check_connection


        # ---1. 같은 가격 40번은 멈춘 것
        if floatBTCPrice == intPriceSave:
            intPriceSaveCnt += 1
        else:
            intPriceSave = floatBTCPrice
            intPriceSaveCnt = 0

        if intPriceSaveCnt > PRICE_SAVE_CNT_LIMIT:  # 같은 가격 15번이면 에러
            print('Same Price ', intPriceSaveCnt, ' times. This is Error')
            is_trade_go = False
            #intPriceSaveCnt = 10
            self.exit()

        # ---2. 청산가 > 평단 = 공매도 떠버린것
        # if LiqPrice > intAvgPurchasePrice:#
        #     print('LiqPrice is bigger than AVG cost, Error')
        #     #is_trade_go = False
        #     #self.exit()
        #     self.RecoverTradingMiss()

    def getOnOff(self):
        global recent_profit_time, floatProfitRate, start_state
        url = 'http://btcatm.cafe24.com/bcfree7/out.php?serial=' + serial
        u = urllib.request.urlopen(url)
        data = u.read()
        conn_string = json.loads(data.decode('utf-8'))
        if len(str(conn_string['c_1'])) > 10:
            start_state = int(conn_string['onoff'])

    def run_loop(self):
        print('/////////////////////////', sys._getframe().f_code.co_name, '/////////////////////////')

        global isExitImmed, restart_working, SYMBOL

        is_trade_go = True  # False 되면 마지막 됌
        is_first_start = True  # 최초시작
        isExitImmed = False
        restart_working = True  # temp
        self.exchange.cancel_all_orders()

        while is_trade_go:
            print('//////////////////////----START----////////////////////////////')
            sys.stdout.write("-----\n")
            sys.stdout.flush()

            # self.check_file_change()
            sleep(settings.LOOP_INTERVAL)

            # This will restart on very short downtime, but if it's longer,
            # the MM will crash entirely as it is unable to connect to the WS on boot.
            if not self.check_connection():
                logger.error("Realtime data connection unexpectedly closed, restarting.")
                self.restart()

            if isExitNextTime:
                is_trade_go = False
            if start_state == STOP_NOW:
                raise print('STOP IMME')
                break
            # print('hah')
            self.getOnOff()
            self.sanity_check()  # Ensures health of mm - several cut-out points here
            self.print_status()  # Print skew, delta, etc
            self.chkError()  #Chk Error

            if start_state == RURNNING or start_state == STOP_AFTER_SELL:
                if not isNowTrade:#정상 매매
                    ##2. 매수 매도가 계산#
                    self.exchange.cancel_all_orders()
                    self.exchange.update_margin(settings.SYMBOL, LEVERAGE)  # set leverage

                    ##3. 매수 주문 넣기#
                    self.getMovingAvg()
                    self.setLongShort()
                    self.place_purchase_orders()
                    self.get_order_success()

                elif not is_first_start and isNowTrade:
                    ##4. 매도 주문 넣기#
                    self.place_sell_orders(floatProfitRate)
                    self.place_sell_orders_losscut()
                elif is_first_start and isNowTrade:#복구할 대상이 있음
                    ##6. 복구 매수 주문 넣기#
                    # self.place_purchase_orders()

                    self.get_order_success()

                else:
                    print('뭥미??')
            self.sendTradeVal() #정보 보내기
            sleep(8)
            is_first_start = False

    def restart(self):
        logger.info("Restarting the market maker...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

#
# Helpers
#


def XBt_to_XBT(XBt):
    return float(XBt) / constants.XBt_TO_XBT


def cost(instrument, quantity, price):
    mult = instrument["multiplier"]
    P = mult * price if mult >= 0 else mult / price
    return abs(quantity * P)


def margin(instrument, quantity, price):
    return cost(instrument, quantity, price) * instrument["initMargin"]


def run():
    logger.info('BitMEX Market Maker Version: %s\n' % constants.VERSION)
    # print('haha')

    om = OrderManager()
    # Try/except just keeps ctrl-c from printing an ugly stacktrace
    try:
        # print('haha')

        om.run_loop()
    except (KeyboardInterrupt, SystemExit):
        print()
        # sys.exit()
