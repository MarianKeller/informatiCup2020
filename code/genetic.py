import json
import math
import os
import glob
import threading
import time
import re
from collections import namedtuple
from copy import deepcopy
from threading import Thread
from time import sleep
from typing import Callable, Dict, List, Tuple

import jsonlines
import matplotlib.pyplot as plt
import numpy as np
import bottle
from bottle import BaseRequest, post, request, route, run, Bottle

from fitnessServer import FitnessServer
from individual import Individual
from postprocessor import numPossibleActions
from preprocessor import inputVectorSize
from playerServer import PlayerServer

genPath = "gens/"

class Population:
    @staticmethod
    def __createIndividual(lowerLimit, upperLimit, shape):
        return Individual(np.random.uniform(low=lowerLimit, high=upperLimit, size=shape))

    @staticmethod
    def __createPopulation(populationSize, lowerLimit, upperLimit, shape: Tuple[int, ...]):
        return [Population.__createIndividual(lowerLimit, upperLimit, shape) for i in range(populationSize)]

    @staticmethod
    def __rouletteSelect(population, cumulativeFitness):
        pick = np.random.uniform(0, cumulativeFitness)
        current = 0
        for individual in population:
            current += individual.fitness
            if current > pick:
                return individual

    @staticmethod
    def __tournamentSelect(population, tournamentSize, numSelect):
        competitorIndices = np.random.choice(len(population), tournamentSize)
        competitors = [population[i] for i in competitorIndices]
        competitors.sort(key=lambda x: x.fitness, reverse=True)
        winners = [competitors[i] for i in range(numSelect)]
        return winners

    @staticmethod
    def __cumulativeFitness(population):
        return sum(individual.fitness for individual in population)

    @staticmethod
    def __select(population, numSurvivors, elitism):
        """using roulette wheel selection"""
        elit = max(population, key=lambda x: x.fitness)
        cumulativeFitness = Population.__cumulativeFitness(population)
        survivors = [Population.__rouletteSelect(
            population, cumulativeFitness) for i in range(numSurvivors)]
        if elitism:
            bestSurvivor = max(survivors, key=lambda x: x.fitness)
            if bestSurvivor.fitness < elit.fitness:
                low = 0
                high = len(survivors) - 1
                rnd = 0 if low == high else np.random.randint(
                    low=low, high=high)
                del survivors[rnd]
                survivors.insert(0, elit)
        return survivors

    @staticmethod
    def __pair(population, numBabies, tournamentSize):
        """using tournament selection"""
        return [(Population.__tournamentSelect(population, tournamentSize, 2)) for i in range(numBabies)]

    @staticmethod
    def __mate(parentList):
        babies = []
        for father, mother in parentList:
            baby = Individual(np.empty(father.genome.shape))
            for i in range(baby.genome.shape[0]):
                choice = np.random.choice([False, True])
                baby.genome[i] = father.genome[i] if choice else mother.genome[i]
            babies.append(baby)
        return babies

    @staticmethod
    def __mutate(generation, lowerLimit, upperLimit, mutationRate, elitism):
        mutatedGeneration = deepcopy(generation)
        start = 0 if not elitism else 1
        for i in range(start, len(mutatedGeneration)):
            genome = mutatedGeneration[i].genome
            for j in range(genome.shape[0]):
                if np.random.choice([True, False], p=[mutationRate, 1-mutationRate]):
                    genome[j] = np.random.uniform(
                        low=lowerLimit, high=upperLimit, size=genome[j].shape)
        return mutatedGeneration

    def __init__(self, fitnessFunction: Callable, populationSize, lowerLimit, upperLimit, shape, tournamentSize,
                 selectionPressure=0.5, mutationRate=0.01, elitism=True,generation=0, activePopulation=None, graveyard: List = []):
        # note: selection mechanism requires fitness >= 0
        self.fitnessFunction = fitnessFunction
        self.populationSize = populationSize
        self.lowerLimit = lowerLimit
        self.upperLimit = upperLimit
        self.shape = shape
        self.selectionPressure = selectionPressure
        self.tournamentSize = tournamentSize
        self.mutationRate = mutationRate
        self.elitism = elitism
        self.activePopulation = activePopulation
        self.lastGeneration = None
        self.evolutionLock = threading.Lock()
        self.generation = generation

    def __cleanup(self):
        self.lastGeneration = deepcopy(self.activePopulation)
        self.lastGeneration.sort(key=lambda x: x.fitness, reverse=True)
        self.generation += 1
        self.evolutionLock.release()

    def __evaluateGeneration(self, callback):
        self.fitnessFunction(self.activePopulation, callback)

    def __applyGeneticOperators(self):
        finalPopulationSize = len(self.activePopulation)
        numSurvivors = math.ceil(
            (1 - self.selectionPressure) * len(self.activePopulation))
        numBabies = finalPopulationSize - numSurvivors

        population = Population.__select(
            self.activePopulation, numSurvivors, self.elitism)
        parentList = Population.__pair(
            population, numBabies, self.tournamentSize)
        babies = Population.__mate(parentList)
        newGeneration = population
        newGeneration.extend(babies)
        newGeneration = Population.__mutate(
            newGeneration, self.lowerLimit, self.upperLimit, self.mutationRate, self.elitism)

        self.activePopulation = newGeneration

    def evolve(self):
        self.evolutionLock.acquire()
        if self.activePopulation is None:
            self.activePopulation = Population.__createPopulation(
                self.populationSize, self.lowerLimit, self.upperLimit, self.shape)
        else:
            self.__applyGeneticOperators()
        self.__evaluateGeneration(callback=self.__cleanup)


def savePopulation(p):
    lastGenList = [
        {"genome": individual.genome.tolist(),
         "fitness": individual.fitness}
        for individual in p.lastGeneration
    ]
    if not os.path.exists(genPath):
        os.makedirs(genPath)
    with jsonlines.open(genPath + "gen" + str(p.generation) + ".jsonl", mode="w") as writer:
        for individual in lastGenList:
            writer.write(individual)


minFitnesses = []
maxFitnesses = []
avgFitnesses = []
mdnFitnesses = []
stdFitnesses = []


def updateStats(p):
    fitnesses = [individual.fitness for individual in p.lastGeneration]
    minFitnesses.append(min(fitnesses))
    maxFitnesses.append(max(fitnesses))
    avgFitnesses.append(np.average(fitnesses))
    mdnFitnesses.append(np.median(fitnesses))
    stdFitnesses.append(np.std(fitnesses))


def printStats(p):
    print('gen:', str(p.generation) + ',', 'min:', str(minFitnesses[-1]) + ',', 'max:', str(maxFitnesses[-1]) +
          ',', 'average:', str(avgFitnesses[-1]) + ',', 'median:', str(mdnFitnesses[-1]) + ',', 'standard deviation:', str(stdFitnesses[-1]))


def plotGraph():
    fig = plt.figure(num="fitness stats")
    ax = plt.axes()
    plt.xlim(1, 100)
    plt.ylim(0, 1)
    plt.title("fitness stats")
    plt.xlabel("generation") 
    plt.ylabel("fitness")
    minFitness, = plt.plot([], [], label="min")
    maxFitness, = plt.plot([], [], label="max")
    avgFitness, = plt.plot([], [], label="avg")
    mdnFitness, = plt.plot([], [], label="mdn")
    stdFitness, = plt.plot([], [], label="std")
    plt.legend()
    while True:
        xlimMin = max(1, len(maxFitnesses) - 99)
        xlimMax = xlimMin + 99
        plt.xlim(xlimMin, xlimMax)
        minFitness.set_xdata(range(1, len(minFitnesses) + 1))
        minFitness.set_ydata(minFitnesses)
        maxFitness.set_xdata(range(1, len(maxFitnesses) + 1))
        maxFitness.set_ydata(maxFitnesses)
        avgFitness.set_xdata(range(1, len(avgFitnesses) + 1))
        avgFitness.set_ydata(avgFitnesses)
        mdnFitness.set_xdata(range(1, len(mdnFitnesses) + 1))
        mdnFitness.set_ydata(mdnFitnesses)
        stdFitness.set_xdata(range(1, len(stdFitnesses) + 1))
        stdFitness.set_ydata(stdFitnesses)
        ax.relim()
        ax.autoscale_view()
        plt.draw()
        plt.pause(10)


def getLatestGenome():
    fileList = glob.glob(genPath + "/*.jsonl")
    dre = re.compile(r'(\d+)')
    fileList.sort(key=lambda l: [int(s) if s.isdigit() else s.lower() for s in re.split(dre, l)])
    latestPopulationFile = fileList.pop()

    with open(latestPopulationFile, 'r') as json_file:
        json_list = list(json_file)
    
    jsonLine = json.loads(json_list[0])
    genome = np.array(jsonLine.get("genome"))

    return genome

def getLatestPopulation():
    fileList = glob.glob(genPath + "/*.jsonl")
    dre = re.compile(r'(\d+)')
    fileList.sort(key=lambda l: [int(s) if s.isdigit() else s.lower() for s in re.split(dre, l)])
    latestPopulationFile = fileList.pop()

    generationNumber = int(re.findall("\d+", latestPopulationFile).pop()) # Get the generation number from the input filename

    with open(latestPopulationFile, 'r') as json_file:
        json_list = list(json_file)

    activePopulation = []
    for json_str in json_list:
        result = json.loads(json_str)
        genome = np.array(result.get("genome"))
        fitness = result.get("fitness")
        dude = Individual(genome=genome, fitness=fitness, ID=FitnessServer.getGenomeId(genome))
        activePopulation.append(dude)
    return activePopulation, generationNumber

def startEvolution(startFromLastGenome=False):
    #plotThread = threading.Thread(target=plotGraph)
    #plotThread.start()
    fs = FitnessServer()
    if startFromLastGenome:
        activePopulation, generationNumber = getLatestPopulation()
        populationSize = len(activePopulation)
    else:
        activePopulation = None
        generationNumber = 0
        populationSize = 20
    p = Population(fitnessFunction=lambda pop, callb: fs.evaluateGenomes(pop, callb), populationSize=populationSize,
                   lowerLimit=-1, upperLimit=1, shape=(numPossibleActions, inputVectorSize), tournamentSize=4,
                   elitism=True, mutationRate=0.1, selectionPressure=0.25, generation=generationNumber, activePopulation=activePopulation)

    for _ in range(1000):
        if p.lastGeneration:
            savePopulation(p)
            updateStats(p)
            printStats(p)
        p.evolve()

def startGame():
    genome = getLatestGenome()
    pl = PlayerServer(genome=genome)
    route("/play", "POST", pl.gamePlayer)



@post("/main")
def main():
    incomingRequest = request.json
    response = {}
    if "evolve" in incomingRequest:
        if incomingRequest.get("startFromLast", False):
            thread = Thread(target=startEvolution, args=(True,))
            response["sessionType"] = "training"
        else:
            thread = Thread(target=startEvolution, args=(False,))
    elif "play" in incomingRequest:
        thread = Thread(target=startGame)
        response["sessionType"] = "playing"
    thread.start()
    return response

BaseRequest.MEMFILE_MAX = 10 * 1024 * 1024
run(host=FitnessServer.playerServerIp,
    port=FitnessServer.playerServerPort, quiet=True)