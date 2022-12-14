"""
Read position file, get coin pairs in position and check if is time to sell, get coin pairs not in position and check if its time to buy.  

Gets all coin pairs from Binance, calculate market phase for each and store results in coinpairByMarketPhase_USD_1d.csv 
Removes coins from positions files that are not in the accumulation or bullish phase.
Adds the coins in the accumulation or bullish phase to addCoinPair.csv and calc BestEMA 
for each coin pair on 1d,4h,1h time frame and save on positions files
"""


import os
import re
#from turtle import left
from xml.dom import ValidationErr
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException
from binance import BinanceSocketManager
from binance.helpers import round_step_size
import requests
from datetime import datetime
import time
import sys
import math
import numpy as np
#import dataframe_image as dfi
from numbers import Number
from typing import Sequence
from backtesting.lib import crossover

# %%
# environment variables
try:
    # Binance
    api_key = os.environ.get('binance_api')
    api_secret = os.environ.get('binance_secret')
    telegram_chat_id = os.environ.get('telegram_chat_id')
except KeyError: 
    print("Environment variable does not exist")

# Binance Client
client = Client(api_key, api_secret)

# %%
# constants

# positionscheck file example
# Currency,position,quantity
# BTCBUSD,0,0.0

# strategy
# gTimeframe = client.KLINE_INTERVAL_1HOUR # "1h"
gFastMA = int("8")
gSlowMA = int("34")
gStrategyName = str(gFastMA)+"/"+str(gSlowMA)+" CROSS"

# emoji
eStart   = u'\U000025B6'
eStop    = u'\U000023F9'
eWarning = u'\U000026A0'
eEnterTrade = u'\U0001F91E' #crossfingers
eExitTrade  = u'\U0001F91E' #crossfingers
eTradeWithProfit = u'\U0001F44D' # thumbs up
eTradeWithLoss   = u'\U0001F44E' # thumbs down
eInformation = u'\U00002139'

# run modes 
# test - does not execute orders on the exchange
# prod - execute orders on the exchange

# Check the program has been called with the timeframe
# total arguments
n = len(sys.argv)
# print("Total arguments passed:", n)
if n < 2:
    print("Argument is missing")
    timeframe = input('Enter timeframe (1d, 4h or 1h):')
    runMode = input('Enter run mode (test, prod):')
else:
    # argv[0] in Python is always the name of the script.
    timeframe = sys.argv[1]
    runMode = sys.argv[2]

if timeframe == "1h":
    gTimeFrameNum = int("1")
    gtimeframeTypeShort = "h" # h, d
    gtimeframeTypeLong = "hour" # hour, day
elif timeframe == "4h":
    gTimeFrameNum = int("4")
    gtimeframeTypeShort = "h" # h, d
    gtimeframeTypeLong = "hour" # hour, day
elif timeframe == "1d":
    gTimeFrameNum = int("1")
    gtimeframeTypeShort = "d" # h, d
    gtimeframeTypeLong = "day" # hour, day

# Telegram
telegramToken = os.environ.get('telegramToken'+str(gTimeFrameNum)+gtimeframeTypeShort) 
telegramToken_ClosedPosition = os.environ.get('telegramToken_ClosedPositions') 


# percentage of balance to open position for each trade - example 0.1 = 10%
tradepercentage = float("0.05") # 5%
minPositionSize = float("20.0") # minimum position size in usd

# risk percentage per trade - example 0.01 = 1%
# not developed yet!!
risk = float("0.01")

# create empty dataframes
dfPositions = pd.DataFrame()
dfOrders = pd.DataFrame()
dfBestEMA = pd.DataFrame()

def readCSVfiles():

    global dfPositions
    global dfOrders
    global dfBestEMA

    # read positions
    dfPositions = pd.read_csv('positions'+str(gTimeFrameNum)+gtimeframeTypeShort+'.csv')
    # posframe

    # read orders csv
    # we just want the header, there is no need to get all the existing orders.
    # at the end we will append the orders to the csv
    dfOrders = pd.read_csv('orders'+str(gTimeFrameNum)+gtimeframeTypeShort+'.csv', nrows=0)

    # read best ema cross
    dfBestEMA = pd.read_csv('coinpairBestEma.csv')


# %%
def sendTelegramMessage(emoji, msg):

    if not emoji:
        lmsg = msg
    else:
        lmsg = emoji+" "+msg

    # To fix the issues with dataframes alignments, the message is sent as HTML and wraped with <pre> tag
    # Text in a <pre> element is displayed in a fixed-width font, and the text preserves both spaces and line breaks
    lmsg = "<pre>"+lmsg+"</pre>"

    params = {
    "chat_id": telegram_chat_id,
    "text": lmsg,
    "parse_mode": "HTML",
    }
    
    resp = requests.post("https://api.telegram.org/bot{}/sendMessage".format(telegramToken), params=params).json()


def sendTelegramAlert(emoji, date, coin, timeframe, strategy, ordertype, unitValue, amount, USDValue, pnlPerc = '', pnlUSD = ''):
    lmsg = emoji + " " + str(date) + "\n" + coin + "\n" + strategy + "\n" + timeframe + "\n" + ordertype + "\n" + "UnitPrice: " + str(unitValue) + "\n" + "Qty: " + str(amount)+ "\n" + "USD: " + str(USDValue)
    if pnlPerc != '':
        lmsg = lmsg + "\n"+"PnL%: "+str(round(float(pnlPerc),2)) + "\n"+"PnL USD: "+str(round(float(pnlUSD),2))

    # To fix the issues with dataframes alignments, the message is sent as HTML and wraped with <pre> tag
    # Text in a <pre> element is displayed in a fixed-width font, and the text preserves both spaces and line breaks
    lmsg = "<pre>"+lmsg+"</pre>"

    params = {
    "chat_id": telegram_chat_id,
    "text": lmsg,
    "parse_mode": "HTML",
    }
    
    resp = requests.post("https://api.telegram.org/bot{}/sendMessage".format(telegramToken), params=params).json()

    # if is a closed position send also to telegram of closed positions
    if emoji in [eTradeWithProfit, eTradeWithLoss]:
        resp = requests.post("https://api.telegram.org/bot{}/sendMessage".format(telegramToken_ClosedPosition), params=params).json()

def sendTelegramPhoto(photoName='balance.png'):
    # get current dir
    cwd = os.getcwd()
    limg = cwd+"/"+photoName
    # print(limg)
    oimg = open(limg, 'rb')
    url = f"https://api.telegram.org/bot{telegramToken}/sendPhoto?chat_id={telegram_chat_id}"
    requests.post(url, files={'photo':oimg}) # this sends the message


# %%
# Not working properly yet
def spot_balance():
        sum_btc = 0.0
        balances = client.get_account()
        for _balance in balances["balances"]:
            asset = _balance["asset"]
            if True: #float(_balance["free"]) != 0.0 or float(_balance["locked"]) != 0.0:
                try:
                    btc_quantity = float(_balance["free"]) + float(_balance["locked"])
                    if asset == "BTC":
                        sum_btc += btc_quantity
                    else:
                        _price = client.get_symbol_ticker(symbol=asset + "BTC")
                        sum_btc += btc_quantity * float(_price["price"])
                except:
                    pass

        current_btc_price_USD = client.get_symbol_ticker(symbol="BTCUSDT")["price"]
        own_usd = sum_btc * float(current_btc_price_USD)
        print(" * Spot => %.8f BTC == " % sum_btc, end="")
        print("%.8f USDT" % own_usd)
# spot_balance()

# %%
def calcPositionSize(pStablecoin = 'BUSD'):

    try:
        
        # get balance from BUSD
        stablecoin = client.get_asset_balance(asset=pStablecoin)
        stablecoinBalance = float(stablecoin['free'])
        # print(stableBalance)
    except BinanceAPIException as e:
        sendTelegramMessage(eWarning, e)
        
    # calculate position size based on the percentage per trade
    resultado = stablecoinBalance*tradepercentage 
    resultado = round(resultado, 5)

    if resultado < minPositionSize:
        resultado = minPositionSize

    # make sure there are enough funds otherwise abort the buy position
    if stablecoinBalance < resultado:
        resultado = 0

    return resultado
    
    
    

# %%
def getdata(coinPair, aTimeframeNum, aTimeframeTypeShort, aFastMA=0, aSlowMA=0):

    # update EMAs from the best EMA return ratio
    global gFastMA
    global gSlowMA
    global gStrategyName

    lTimeFrame = str(aTimeframeNum)+aTimeframeTypeShort
    if aTimeframeTypeShort == "h":
        lTimeframeTypeLong = "hour"
    elif aTimeframeTypeShort == "d":
        lTimeframeTypeLong = "day"
    
    if aSlowMA > 0 and aFastMA > 0:
        gFastMA = aFastMA
        gSlowMA = aSlowMA
    else:
        listEMAvalues = dfBestEMA[(dfBestEMA.coinPair == coinPair) & (dfBestEMA.timeFrame == lTimeFrame)]

        if not listEMAvalues.empty:
            gFastMA = int(listEMAvalues.fastEMA.values[0])
            gSlowMA = int(listEMAvalues.slowEMA.values[0])
        else:
            gFastMA = int("0")
            gSlowMA = int("0")

    gStrategyName = str(gFastMA)+"/"+str(gSlowMA)+" EMA cross"

    # if bestEMA does not exist return empty dataframe in order to no use that trading pair
    if gFastMA == 0:
        frame = pd.DataFrame()
        return frame
    
    # if best Ema exist get price data 
    # lstartDate = str(1+gSlowMA*aTimeframeNum)+" "+lTimeframeTypeLong+" ago UTC"
    sma200 = 200
    lstartDate = str(sma200*aTimeframeNum)+" "+lTimeframeTypeLong+" ago UTC" 
    ltimeframe = str(aTimeframeNum)+aTimeframeTypeShort
    frame = pd.DataFrame(client.get_historical_klines(coinPair,
                                                    ltimeframe,
                                                    lstartDate))

    frame = frame[[0,4]]
    frame.columns = ['Time','Close']
    frame.Close = frame.Close.astype(float)
    frame.Time = pd.to_datetime(frame.Time, unit='ms')
    return frame

#-----------------------------------------------------------------------
# calculates moving averages 
#-----------------------------------------------------------------------
def applytechnicals(df, aFastMA, aSlowMA):
    
    if aFastMA > 0: 
        df['FastEMA'] = df['Close'].ewm(span=aFastMA, adjust=False).mean()
        df['SlowEMA'] = df['Close'].ewm(span=aSlowMA, adjust=False).mean()
        df['SMA50']  = df['Close'].rolling(50).mean()
        df['SMA200'] = df['Close'].rolling(200).mean()
#-----------------------------------------------------------------------

#-----------------------------------------------------------------------
# Update positions files 
#-----------------------------------------------------------------------
def changepos(dfPos, curr, order, typePos, buyPrice=0, currentPrice=0):

    # type = buy, sell or updatePnL

    if typePos == "buy":
        dfPos.loc[dfPos['Currency'] == curr, ['position','quantity','buyPrice']] = [1,float(order['executedQty']),float(buyPrice)]
    elif typePos == "sell":
        dfPos.loc[dfPos['Currency'] == curr, ['position','quantity','buyPrice','currentPrice','PnLperc']] = [0,0,0,0,0]
    elif typePos == "updatePnL":
        pos = dfPos.loc[dfPos['Currency'] == curr]
        if len(pos) > 0:
            lBuyPrice = pos['buyPrice'].values[0]
            if not math.isnan(lBuyPrice) and (lBuyPrice > 0):
                PnLperc = ((currentPrice-lBuyPrice)/lBuyPrice)*100
                PnLperc = round(PnLperc, 2)
                dfPos.loc[dfPos['Currency'] == curr, ['currentPrice','PnLperc']] = [currentPrice,PnLperc]

    dfPos.to_csv('positions'+str(gTimeFrameNum)+gtimeframeTypeShort+'.csv', index=False)
#-----------------------------------------------------------------------

def removeCoinsPosition():
    # remove coin pairs from position file not in accumulation or bullish phase -> coinpairByMarketPhase_BUSD_1d.csv
    
    dfAllByMarketPhase = pd.read_csv('coinpairByMarketPhase_BUSD_1d.csv')
    dfBullish = dfAllByMarketPhase.query("MarketPhase == 'bullish'")
    dfAccumulation= dfAllByMarketPhase.query("MarketPhase == 'accumulation'")
    # union accumulation and bullish results
    dfUnion = pd.concat([dfBullish, dfAccumulation], ignore_index=True)
    accuBullishCoinPairs = dfUnion.Coinpair.to_list()

    positionsfile = pd.read_csv('positions'+str(gTimeFrameNum)+gtimeframeTypeShort+'.csv')

    filter1 = (positionsfile['position'] == 1) & (positionsfile['quantity'] > 0)
    filter2 = positionsfile['Currency'].isin(accuBullishCoinPairs)
    positionsfile = positionsfile[filter1 | filter2]

    # order by name
    positionsfile.sort_values(by=['Currency'], inplace=True)

    positionsfile.to_csv('positions'+str(gTimeFrameNum)+gtimeframeTypeShort+'.csv', index=False)

# %%
def adjustSize(coin, amount):

    # sendTelegramMessage("", "adjust size")
    
    for filt in client.get_symbol_info(coin)['filters']:
        if filt['filterType'] == 'LOT_SIZE':
            stepSize = float(filt['stepSize'])
            minQty = float(filt['minQty'])
            break

    order_quantity = round_step_size(amount, stepSize)
    return order_quantity


def calcPnL(symbol, sellprice: float, sellqty: float):
    # open orders file to search last buy order for the coin and time frame provided on the argument.
    with open(r"orders"+str(gTimeFrameNum)+gtimeframeTypeShort+".csv", 'r') as fp:
        for l_no, line in reversed(list(enumerate(fp))):
            # search string
            if (symbol in line) and ("BUY" in line):

                # print('string found in a file')
                # print('Line Number:', l_no)
                # print('Line:', line)
                
                # sellprice = 300
                orderid = line.split(',')[0]
                # print('orderid:', orderid)
                buyprice = float(line.split(',')[4])
                # print('Buy Price:', buyprice)
                buyqty = float(line.split(',')[5])
                # print('Buy qty:', buyqty)
                # print('Sell price:', sellprice)
                # sellqty = buyqty
                # PnLperc = ((sellprice-buyprice)/buyprice)*100
                PnLperc = (((sellprice*sellqty)-(buyprice*buyqty))/(buyprice*buyqty))*100
                PnLperc = round(PnLperc, 2)
                PnLvalue = (sellprice*sellqty)-(buyprice*buyqty) # erro!
                PnLValue = round(PnLvalue, 2)
                # print('Buy USD =', round(buyprice*buyqty,2))
                # print('Sell USD =', round(sellprice*sellqty,2))
                # print('PnL% =', PnLperc)
                # print('PnL USD =', PnLvalue)
                
                
                lista = [orderid, PnLperc, PnLvalue]
                return lista
                
                break

# %%
def trader():

    # remove coin pairs from position file not in accumulation or bullish phase -> coinpairByMarketPhase_BUSD_1d.csv
    removeCoinsPosition()

    # read position, orders and bestEma files
    readCSVfiles()

    # list of coins in position - SELL
    listPosition1 = dfPositions[dfPositions.position == 1].Currency
    # list of coins in position - BUY
    listPosition0 = dfPositions[dfPositions.position == 0].Currency

    # ------------------------------------------------------------
    # check open positions and SELL if conditions are fulfilled 
    # ------------------------------------------------------------
    for coinPair in listPosition1:
        # sendTelegramMessage("",coinPair) 
        df = getdata(coinPair, gTimeFrameNum, gtimeframeTypeShort)

        if df.empty:
            print(f'{coinPair} - {gStrategyName} - Best EMA values missing')
            sendTelegramMessage(eWarning,f'{coinPair} - {gStrategyName} - Best EMA values missing')
            continue

        applytechnicals(df, gFastMA, gSlowMA)
        # lastrow = df.iloc[-1]

        # separate coin from stable. example coinPair=BTCUSDT coinOnly=BTC coinStable=USDT 
        coinOnly = coinPair[:-4]
        coinStable = coinPair[-4:]

        # if lastrow.SlowMA > lastrow.FastMA:
        if crossover(df.SlowEMA, df.FastEMA): 
            try:
                balanceQty = float(client.get_asset_balance(asset=coinOnly)['free'])  
            except BinanceAPIException as ea:
                sendTelegramMessage(eWarning, ea)

            buyOrderQty = float(dfPositions[dfPositions.Currency == coinPair].quantity.values[0])
            sellQty = buyOrderQty
            if balanceQty < buyOrderQty:
                sellQty = balanceQty
            sellQty = adjustSize(coinPair, sellQty)

            if sellQty > 0: 
                
                try:        
                    if runMode == "prod":
                        order = client.create_order(symbol=coinPair,
                                                side=client.SIDE_SELL,
                                                type=client.ORDER_TYPE_MARKET,
                                                quantity = sellQty
                                                )
                        
                        fills = order['fills']
                        avg_price = sum([float(f['price']) * (float(f['qty']) / float(order['executedQty'])) for f in fills])
                        avg_price = round(avg_price,8)

                        # update position file with the sell order
                        # changepos(dfPositions, coinPair,'',buy=False)
                        changepos(dfPositions, coinPair, '', typePos="sell")

                except BinanceAPIException as ea:
                    sendTelegramMessage(eWarning, ea)
                except BinanceOrderException as eo:
                    sendTelegramMessage(eWarning, eo)

                #add new row to end of DataFrame
                if runMode == "prod":
                    addPnL = calcPnL(coinPair, float(avg_price), float(order['executedQty']))
                    dfOrders.loc[len(dfOrders.index)] = [order['orderId'], pd.to_datetime(order['transactTime'], unit='ms'), coinPair, 
                                                        order['side'], avg_price, order['executedQty'],
                                                        addPnL[0], # buyorderid 
                                                        addPnL[1], # PnL%
                                                        addPnL[2]  # PnL USD
                                                        ]
                

                    # print(order)
                    # sendTelegramMessage(eExitTrade, order)
                    if addPnL[2] > 0: 
                        emojiTradeResult = eTradeWithProfit
                    else:
                        emojiTradeResult = eTradeWithLoss

                    sendTelegramAlert(emojiTradeResult,
                                    # order['transactTime']
                                    pd.to_datetime(order['transactTime'], unit='ms'), 
                                    order['symbol'], 
                                    str(gTimeFrameNum)+gtimeframeTypeShort, 
                                    gStrategyName,
                                    order['side'],
                                    avg_price,
                                    order['executedQty'],
                                    avg_price*float(order['executedQty']),
                                    addPnL[1], # PnL%
                                    addPnL[2]  # PnL USD
                                    )
            else:
                if runMode == "prod":
                    # if there is no qty on balance to sell we set the qty on positions file to zero
                    # this can happen if we sell on the exchange (for example, due to a pump) before the bot sells it. 
                    # changepos(dfPositions, coinPair,'',buy=False)
                    changepos(dfPositions, coinPair, '', typePos="sell")
        else:
            print(f'{coinPair} - {gStrategyName} - Sell condition not fulfilled')
            sendTelegramMessage("",f'{coinPair} - {gStrategyName} - Sell condition not fulfilled')
            
            # set current PnL
            lastrow = df.iloc[-1]
            currentPrice = lastrow.Close
            # changepos(dfPositions, coinPair,'',buy=False)
            changepos(dfPositions, coinPair, '', typePos="updatePnL", currentPrice=currentPrice)


    # ------------------------------------------------------------------
    # check coins not in positions and BUY if conditions are fulfilled
    # ------------------------------------------------------------------
    for coinPair in listPosition0:
        # sendTelegramMessage("",coinPair) 
        df = getdata(coinPair, gTimeFrameNum, gtimeframeTypeShort)

        if df.empty:
            print(f'{coinPair} - {gStrategyName} - Best EMA values missing')
            sendTelegramMessage(eWarning,f'{coinPair} - {gStrategyName} - Best EMA values missing')
            continue

        applytechnicals(df, gFastMA, gSlowMA)
        lastrow = df.iloc[-1]

        # separate coin from stable. example coinPair=BTCUSDT coinOnly=BTC coinStable=USDT 
        coinOnly = coinPair[:-4]
        # print('coinOnly=',coinOnly)
        coinStable = coinPair[-4:]
        # print('coinStable=',coinStable)

        accumulationPhase = (lastrow.Close > lastrow.SMA50) and (lastrow.Close > lastrow.SMA200) and (lastrow.SMA50 < lastrow.SMA200)
        bullishPhase = (lastrow.Close > lastrow.SMA50) and (lastrow.Close > lastrow.SMA200) and (lastrow.SMA50 > lastrow.SMA200)
        
        # if lastrow.FastMA > lastrow.SlowMA:
        if (accumulationPhase or bullishPhase) and crossover(df.FastEMA, df.SlowEMA):
            positionSize = calcPositionSize(pStablecoin=coinStable)
            # sendTelegramMessage("", "calc position size 5")
            # print("positionSize: ", positionSize)
            # sendTelegramMessage('',client.SIDE_BUY+" "+coinPair+" BuyStableQty="+str(positionSize))  
            if positionSize > 0:
                try:
                    if runMode == "prod":
                        order = client.create_order(symbol=coinPair,
                                                    side=client.SIDE_BUY,
                                                    type=client.ORDER_TYPE_MARKET,
                                                    quoteOrderQty = positionSize,
                                                    newOrderRespType = 'FULL') 
                        
                        fills = order['fills']
                        avg_price = sum([float(f['price']) * (float(f['qty']) / float(order['executedQty'])) for f in fills])
                        avg_price = round(avg_price,8)
                        # print('avg_price=',avg_price)

                        # update positions file with the buy order
                        # changepos(dfPositions, coinPair,order,buy=True,buyPrice=avg_price)
                        changepos(dfPositions, coinPair, order, typePos="buy", buyPrice=avg_price)

                except BinanceAPIException as ea:
                    sendTelegramMessage(eWarning, ea)
                except BinanceOrderException as eo:
                    sendTelegramMessage(eWarning, eo)
                
                #add new row to end of DataFrame
                if runMode == "prod":
                    dfOrders.loc[len(dfOrders.index)] = [order['orderId'], pd.to_datetime(order['transactTime'], unit='ms'), coinPair, 
                                                        order['side'], avg_price, order['executedQty'],
                                                        0,0,0]
                            
                    
                    sendTelegramAlert(eEnterTrade,
                                    # order['transactTime'], 
                                    pd.to_datetime(order['transactTime'], unit='ms'),
                                    order['symbol'], 
                                    str(gTimeFrameNum)+gtimeframeTypeShort, 
                                    gStrategyName,
                                    order['side'],
                                    avg_price,
                                    order['executedQty'],
                                    positionSize)
            else:
                sendTelegramMessage(eWarning,client.SIDE_BUY+" "+coinPair+" - Not enough "+coinStable+" funds!")
                
        else:
            print(f'{coinPair} - {gStrategyName} - Buy condition not fulfilled')
            sendTelegramMessage("",f'{coinPair} - {gStrategyName} - Buy condition not fulfilled')

    # remove coin pairs from position file not in accumulation or bullish phase -> coinpairByMarketPhase_BUSD_1d.csv
    # this is needed here specially for 1h/4h time frames when coin is no longer on bullish or accumulation and a close position occurred
    # and we dont want to back in position during the same day
    removeCoinsPosition()


def main():
    # inform that is running
    sendTelegramMessage(eStart,"Binance Trader Bot - Start")

    trader()

    # add orders to csv file
    dfOrders.to_csv('orders'+str(gTimeFrameNum)+gtimeframeTypeShort+'.csv', mode='a', index=False, header=False)


    # posframe.drop('position', axis=1, inplace=True)
    # posframe.style.applymap(custom_style)
     
    # send balance
    print(dfPositions)

    sendTelegramMessage("",dfPositions.to_string())

    # dfi.export(posframe, 'balance.png', fontsize=8, table_conversion='matplotlib')
    # sendTelegramPhoto()


    # inform that ended
    sendTelegramMessage(eStop, "Binance Trader Bot - End")

if __name__ == "__main__":
    main()



