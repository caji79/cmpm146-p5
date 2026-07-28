import copy
import heapq
import metrics
import multiprocessing.pool as mpool
import os
import random
import shutil
import time
import math

width = 200
height = 16

SELECTION_METHOD = "tournament"  # or "roulette"

options = [
    "-",  # an empty space
    "X",  # a solid wall
    "?",  # a question mark block with a coin
    "M",  # a question mark block with a mushroom
    "B",  # a breakable block
    "o",  # a coin
    "|",  # a pipe segment
    "T",  # a pipe top
    "E",  # an enemy
    #"f",  # a flag, do not generate
    #"v",  # a flagpole, do not generate
    #"m"  # mario's start position, do not generate
]

# The level as a grid of tiles


class Individual_Grid(object):
    __slots__ = ["genome", "_fitness"]

    def __init__(self, genome):
        self.genome = copy.deepcopy(genome)
        self._fitness = None

    # Update this individual's estimate of its fitness.
    # This can be expensive so we do it once and then cache the result.
    def calculate_fitness(self):
        measurements = metrics.metrics(self.to_level())
        # Print out the possible measurements or look at the implementation of metrics.py for other keys:
        # print(measurements.keys())
        # Default fitness function: Just some arbitrary combination of a few criteria.  Is it good?  Who knows?
        # STUDENT Modify this, and possibly add more metrics.  You can replace this with whatever code you like.
        coefficients = dict(
            meaningfulJumpVariance=0.5,
            negativeSpace=0.6,
            pathPercentage=0.5,
            emptyPercentage=0.6,
            linearity=-0.5,
            solvability=2.0
        )
        self._fitness = sum(map(lambda m: coefficients[m] * measurements[m],
                                coefficients))
        # Bonus for decoration variety
        dec = measurements.get("decorationPercentage", 0)
        if dec > 0.01:
            self._fitness += min(dec * 5.0, 0.5)
        # Penalty for too easy or too hard
        leniency = measurements.get("leniency", 0)
        if leniency > 15:
            self._fitness -= 0.3
        if leniency < -5:
            self._fitness -= 0.3
        # Bonus for meaningful jumps
        mj = measurements.get("meaningfulJumps", 0)
        if mj > 0:
            self._fitness += min(mj * 0.1, 0.5)
        jumps = measurements.get("jumps", 0)
        if jumps > 2:
            self._fitness += min(jumps * 0.02, 0.3)
        return self

    # Return the cached fitness value or calculate it as needed.
    def fitness(self):
        if self._fitness is None:
            self.calculate_fitness()
        return self._fitness

    # Mutate a genome into a new genome.  Note that this is a _genome_, not an individual!
    def mutate(self, genome):
        # STUDENT implement a mutation operator, also consider not mutating this individual
        # STUDENT also consider weighting the different tile types so it's not uniformly random
        # STUDENT consider putting more constraints on this to prevent pipes in the air, etc
        left = 1
        right = width - 1
        mutation_rate = 0.001
        mutation_tiles = ["-", "X", "?", "M", "B", "o", "E"]
        mutation_weights = [120, 10, 4, 2, 2, 6, 4]

        for y in range(height - 1):
            for x in range(left, right):
                # don't mutate pipe body and head
                if genome[y][x] in ("T", "|"):
                    continue

                # Don't mutate the starting area
                if x < 5:
                    continue
                # Don't mutate near the flag
                if x >= width - 5:
                    continue
                
                if random.random() < mutation_rate:
                    old_tile = genome[y][x]

                    # randomly pick one of the mutation tiles and replace the old one
                    new_tile = random.choices(
                        mutation_tiles,
                        weights=mutation_weights,
                        k=1
                    )[0]

                    # prevent picking the same type of tile
                    while new_tile == old_tile:
                        new_tile = random.choices(
                            mutation_tiles,
                            weights=mutation_weights,
                            k=1
                        )[0]

                    # Enemies should only be on ground level or on platforms
                    if new_tile == "E":
                        if y < height - 2:
                            # Check if there's ground below
                            if genome[y + 1][x] not in ("X", "B", "?", "M"):
                                new_tile = "-"
                    # Blocks shouldn't be on the very bottom or very top
                    if new_tile in ("X", "B", "?", "M"):
                        if y >= height - 2 or y <= 1:
                            new_tile = "-"

                    genome[y][x] = new_tile

        # Occasionally add a gap
        if random.random() < 0.03:
            gx = random.randint(10, width - 15)
            gw = random.randint(2, 4)
            for i in range(gw):
                if gx + i < right:
                    genome[height - 1][gx + i] = "-"

        # If an X block is near an existing wall, build a stair stepping up/down
        if random.random() < 0.04:
            sx = random.randint(5, width - 10)
            sh = random.randint(2, 5)
            direction = random.choice([-1, 1])
            for step in range(sh):
                col = sx + step * direction
                if col < 1 or col >= right:
                    break
                step_height = step + 1
                for fill_y in range(height - 2, height - 2 - step_height, -1):
                    if fill_y >= 4:
                        genome[fill_y][col] = "X"

        # Constraints
        # cap gap widths to 4 so they stay jumpable
        gap_count = 0
        for x in range(1, width - 1):
            if genome[height - 1][x] == "-":
                gap_count += 1
                if gap_count > 4:
                    genome[height - 1][x] = "X"
            else:
                gap_count = 0

        # clear walls/pipes above gaps
        for x in range(1, width - 1):
            if genome[height - 1][x] == "-":
                for y in range(height - 2, -1, -1):
                    if genome[y][x] in ("X", "|", "T"):
                        genome[y][x] = "-"


        # fix floating X walls
        # For each column, find floating X (not connected to ground).
        # If the floating X wall would be height <= 4 when grounded, fill it to ground; if it can form a staircase with neighboring grounded wall, fill it to ground; otherwise, remove the top X tiles from the floating wall
        for x in range(1, width - 1):
            if genome[height - 1][x] != "X":
                continue  # \gaps already handled
            # Find grounded wall height
            grounded_top = height - 1
            for y in range(height - 2, -1, -1):
                if genome[y][x] == "X":
                    grounded_top = y
                else:
                    break
            # find floating X above ground
            floating_xs = []
            for y in range(grounded_top - 1, -1, -1):
                if genome[y][x] == "X":
                    floating_xs.append(y)
            if not floating_xs:
                continue
            # highest floating X
            highest_floating = min(floating_xs)
            # height if fill from highest floating X down to ground
            total_height = (height - 2) - highest_floating + 1
            # neighbor wall heights for staircase check
            can_stair = False
            for nx in [x - 1, x + 1]:
                if 0 <= nx < width and genome[height - 1][nx] == "X":
                    nh = 0
                    for ny in range(height - 2, -1, -1):
                        if genome[ny][nx] == "X":
                            nh += 1
                        else:
                            break
                    if nh > 0 and abs(total_height - nh) <= 2:
                        can_stair = True

            if total_height <= 4 or can_stair:
                # Fill from highest floating X down to ground
                for fill_y in range(highest_floating, height - 1):
                    genome[fill_y][x] = "X"
            else:
                # Remove the floating X
                for y in floating_xs:
                    genome[y][x] = "-"

        # break up vertical stacks of B
        for x in range(1, width - 1):
            b_count = 0
            for y in range(height - 2, -1, -1):
                if genome[y][x] == "B":
                    b_count += 1
                    if b_count >= 2:
                        genome[y][x] = "-"
                else:
                    b_count = 0

        # clear top 4 rows
        for y in range(4):
            for x in range(width):
                genome[y][x] = "-"

        # M and ? must be reachable  (4 spaces below must be solid)
        for x in range(1, width - 1):
            for y in range(4, height - 2):
                if genome[y][x] in ("M", "?"):
                    has_support = False
                    for dy in range(1, 5):
                        if y + dy < height and genome[y + dy][x] in ("X", "B", "?", "M"):
                            has_support = True
                            break
                    if not has_support:
                        genome[y][x] = "-"

        # coins must be reachable (2 spaces below must be solid)
        for x in range(1, width - 1):
            for y in range(4, height - 2):
                if genome[y][x] == "o":
                    has_support = False
                    for dy in range(1, 3):
                        if y + dy < height and genome[y + dy][x] in ("X", "B", "?", "M"):
                            has_support = True
                            break
                    if not has_support:
                        genome[y][x] = "-"

         # enemies should be on ground or on a solid surface
        for x in range(1, width - 1):
            for y in range(height - 2):
                if genome[y][x] == "E":
                    if y + 1 >= height or genome[y + 1][x] not in ("X", "B", "?", "M", "|", "T"):
                        genome[y][x] = "-"

        # no block directly above a pipe top 
        for x in range(1, width - 1):
            for y in range(1, height - 1):
                if genome[y][x] == "T" and y - 1 >= 0:
                    if genome[y - 1][x] != "-":
                        genome[y - 1][x] = "-"        

        # block directly below ?, B, M must be air
        for x in range(1, width - 1):
            for y in range(4, height - 2):
                if genome[y][x] in ("?", "B", "M"):
                    if y + 1 < height - 1 and genome[y + 1][x] in ("?", "B", "M", "X"):
                        genome[y + 1][x] = "-"

        return genome

    # Create zero or more children from self and other
    def generate_children(self, other):
        new_genome1 = copy.deepcopy(self.genome)
        new_genome2 = copy.deepcopy(other.genome)
        # Leaving first and last columns alone...
        # do crossover with other

        left = 1
        right = width - 1
        crossover_point = random.randint(left, right - 1)

        for x in range(left, right):
            # if random.random() < 0.5:
            if x >= crossover_point:
                for y in range(height):
                    new_genome1[y][x] = other.genome[y][x]
                    new_genome2[y][x] = self.genome[y][x]

        # do mutation
        new_genome1 = self.mutate(new_genome1)
        new_genome2 = self.mutate(new_genome2)

        for genome in (new_genome1, new_genome2):
            for x in range(1, width - 1):
                for y in range(height - 1):
                    if genome[y][x] == "|":
                        if (genome[y + 1][x] not in ("|", "X")
                            or not any(genome[above][x] == "T" for above in range(y))):
                            genome[y][x] = "-"

        return (Individual_Grid(new_genome1), Individual_Grid(new_genome2))

    # Turn the genome into a level string (easy for this genome)
    def to_level(self):
        return self.genome

    # These both start with every floor tile filled with Xs
    # STUDENT Feel free to change these
    @classmethod
    def empty_individual(cls):
        g = [["-" for col in range(width)] for row in range(height)]
        g[15][:] = ["X"] * width
        g[14][0] = "m"
        g[7][-1] = "v"
        for col in range(8, 14):
            g[col][-1] = "f"
        for col in range(14, 16):
            g[col][-1] = "X"
        return cls(g)

    @classmethod
    def random_individual(cls):
        # STUDENT consider putting more constraints on this to prevent pipes in the air, etc
        # STUDENT also consider weighting the different tile types so it's not uniformly random
        weights = [
            100,  # empty space
            30,  # solid wall
            10,  # question mark block with a coin
            10,  # question mark block with a mushroom
            20,  # breakable block
            20,  # coin
            5,  # pipe segment
            5,  # pipe top
            10,  # enemy
        ]
        g = [random.choices(options, weights=weights, k=width) for row in range(height)]

        # pipe tops need pipe segments below, remove floating pipes, .max height of 4
        for x in range(0, width - 1):
            for y in range(height - 2, -1, -1):
                if g[y][x] == "T" and (y > (height - 5)):
                    for pipe_y in range(y + 1, height - 1):
                        g[pipe_y][x] = "|"
                elif ((g[y][x] == "T" and (y <= height - 5)) 
                      or (g[y][x] == "|" and (g[y + 1][x] not in ("|", "X")) and (g[y - 1][x] not in ("|", "T")))
                      or not any(g[above][x] == "T" for above in range(y))):
                        g[y][x] = "-"

        # forming a solid wall within height of 4
        for x in range(0, width):
            for y in range(height - 2, -1, -1):
                if g[y][x] == "X" and (y > (height - 6)):
                    for pipe_y in range(y + 1, height - 1):
                        g[pipe_y][x] = "X"
                elif (g[y][x] == "X" and (y <= height - 6)):
                        g[y][x] = "-"

        # Constraint
        # no block directly above a pipe top
        for x in range(1, width - 1):
            for y in range(1, height - 1):
                if g[y][x] == "T" and y - 1 >= 0:
                    if g[y - 1][x] != "-":
                        g[y - 1][x] = "-"

        # enemies should be on ground or on a solid surface
        for x in range(1, width - 1):
            for y in range(height - 2):
                if g[y][x] == "E":
                    if y + 1 >= height or g[y + 1][x] not in ("X", "B", "?", "M", "|", "T"):
                        g[y][x] = "-"

        #question blocks shouldn't be on the ground floor row
        for x in range(1, width - 1):
            if g[height - 2][x] in ("?", "M", "B"):
                g[height - 2][x] = "-"

        #clear the top 4 rows
        for y in range(4):
            for x in range(width):
                g[y][x] = "-"

        # rows 4-5 mostly clear
        for y in range(4, 6):
            for x in range(1, width - 1):
                if g[y][x] != "-":
                    if random.random() < 0.8:
                        g[y][x] = "-"

        # randomly remove isolated floating single blocks
        for y in range(4, height - 2):
            for x in range(1, width - 1):
                if g[y][x] in ("B", "?", "M", "X"):
                    left_solid = g[y][x - 1] in ("B", "?", "M", "X")
                    right_solid = g[y][x + 1] in ("B", "?", "M", "X")
                    below_solid = g[y + 1][x] in ("B", "?", "M", "X", "|", "T")
                    if not left_solid and not right_solid and not below_solid:
                        if random.random() < 0.7:
                            g[y][x] = "-"

        # M and ? must be reachable (4 spaces)
        for x in range(1, width - 1):
            for y in range(4, height - 2):
                if g[y][x] in ("M", "?"):
                    has_support = False
                    for dy in range(1, 5):
                        if y + dy < height and g[y + dy][x] in ("X", "B", "?", "M"):
                            has_support = True
                            break
                    if not has_support:
                        g[y][x] = "-"

        # coins must be reachable (2 spaces)
        for x in range(1, width - 1):
            for y in range(4, height - 2):
                if g[y][x] == "o":
                    has_support = False
                    for dy in range(1, 3):
                        if y + dy < height and g[y + dy][x] in ("X", "B", "?", "M"):
                            has_support = True
                            break
                    if not has_support:
                        g[y][x] = "-"

        # block directly below ?, B, M must be air
        for x in range(1, width - 1):
            for y in range(4, height - 2):
                if g[y][x] in ("?", "B", "M"):
                    if y + 1 < height - 1 and g[y + 1][x] in ("?", "B", "M", "X"):
                        g[y + 1][x] = "-"

        # clear starting area
        for y in range(height - 1):
            for x in range(1, 5):
                if g[y][x] not in ("-", "m"):
                    g[y][x] = "-"

        #clear ending area 
        for y in range(height - 1):
            for x in range(width - 5, width - 1):
                if g[y][x] not in ("-", "f", "v", "X"):
                    g[y][x] = "-"


        for x in g:
            x[0] = "-"
            x[-1] = "-"

        g[15][:] = ["X"] * width
        g[14][0] = "m"
        g[7][-1] = "v"
        for y in range(8, 14):
            g[y][-1] = "f"
        for y in range(14, 16):
            g[y][-1] = "X"
        return cls(g)


def offset_by_upto(val, variance, min=None, max=None):
    val += random.normalvariate(0, variance**0.5)
    if min is not None and val < min:
        val = min
    if max is not None and val > max:
        val = max
    return int(val)


def clip(lo, val, hi):
    if val < lo:
        return lo
    if val > hi:
        return hi
    return val

# Inspired by https://www.researchgate.net/profile/Philippe_Pasquier/publication/220867545_Towards_a_Generic_Framework_for_Automated_Video_Game_Level_Creation/links/0912f510ac2bed57d1000000.pdf


class Individual_DE(object):
    # Calculating the level isn't cheap either so we cache it too.
    __slots__ = ["genome", "_fitness", "_level"]

    # Genome is a heapq of design elements sorted by X, then type, then other parameters
    def __init__(self, genome):
        self.genome = list(genome)
        heapq.heapify(self.genome)
        self._fitness = None
        self._level = None

    # Calculate and cache fitness
    def calculate_fitness(self):
        measurements = metrics.metrics(self.to_level())
        # Default fitness function: Just some arbitrary combination of a few criteria.  Is it good?  Who knows?
        # STUDENT Add more metrics?
        # STUDENT Improve this with any code you like
        coefficients = dict(
            meaningfulJumpVariance=0.5,
            negativeSpace=0.6,
            pathPercentage=0.5,
            emptyPercentage=0.6,
            linearity=-0.5,
            solvability=2.0
        )

        # count the number of design elements in a level
        counts = {}
        for de in self.genome:
            de_type = de[1]
            counts[de_type] = counts.get(de_type, 0) + 1

        penalties = 0
        bonuses = 0

        stairs_count = counts.get("6_stairs", 0)
        hole_count = counts.get("0_hole", 0)
        platform_count = counts.get("1_platform", 0)
        coin_count = counts.get("3_coin", 0)
        qblock_count = counts.get("5_qblock", 0)

        # STUDENT For example, too many stairs are unaesthetic.  Let's penalize that
        # penalize excessive stairs and holes
        if stairs_count > 6:
            penalties -= (stairs_count - 6) * 0.5

        if hole_count > 8:
            penalties -= (hole_count - 8) * 0.5

        if platform_count == 0:
            penalties -= 2

        # Reward a moderate number of coins.
        if 5 <= coin_count <= 20:
            bonuses += 1.2
        elif coin_count > 20:
            penalties -= (coin_count - 20) * 0.1

        # Reward a moderate number of question blocks.
        if 5 <= qblock_count <= 15:
            bonuses += 1
        elif qblock_count > 15:
            penalties -= (qblock_count - 15) * 0.1

        # STUDENT If you go for the FI-2POP extra credit, you can put constraint calculation in here too and cache it in a new entry in __slots__.
        self._fitness = sum(map(lambda m: coefficients[m] * measurements[m],
                                coefficients)) + penalties + bonuses
        return self

    def fitness(self):
        if self._fitness is None:
            self.calculate_fitness()
        return self._fitness

    def mutate(self, new_genome):
        # STUDENT How does this work?  Explain it in your writeup.
        # STUDENT consider putting more constraints on this, to prevent generating weird things
        if random.random() < 0.1 and len(new_genome) > 0:
            to_change = random.randint(0, len(new_genome) - 1)
            de = new_genome[to_change]
            new_de = de
            x = de[0]
            de_type = de[1]
            choice = random.random()
            if de_type == "4_block":
                y = de[2]
                breakable = de[3]
                if choice < 0.33:
                    x = offset_by_upto(x, width / 8, min=1, max=width - 2)
                elif choice < 0.66:
                    y = offset_by_upto(y, height / 2, min=2, max=height - 3)
                else:
                    breakable = not de[3]
                new_de = (x, de_type, y, breakable)
            elif de_type == "5_qblock":
                y = de[2]
                has_powerup = de[3]  # boolean
                if choice < 0.33:
                    x = offset_by_upto(x, width / 8, min=1, max=width - 2)
                elif choice < 0.66:
                    y = offset_by_upto(y, height / 2, min=2, max=height - 3)
                else:
                    has_powerup = not de[3]
                new_de = (x, de_type, y, has_powerup)
            elif de_type == "3_coin":
                y = de[2]
                if choice < 0.5:
                    x = offset_by_upto(x, width / 8, min=1, max=width - 2)
                else:
                    y = offset_by_upto(y, height / 2, min=0, max=height - 1)
                new_de = (x, de_type, y)
            elif de_type == "7_pipe":
                h = de[2]
                if choice < 0.5:
                    x = offset_by_upto(x, width / 8, min=1, max=width - 2)
                else:
                    h = offset_by_upto(h, 2, min=2, max=9)
                new_de = (x, de_type, h)
            elif de_type == "0_hole":
                w = de[2]
                if choice < 0.5:
                    x = offset_by_upto(x, width / 8, min=1, max=width - 2)
                else:
                    w = offset_by_upto(w, 4, min=1, max=width - 2)
                new_de = (x, de_type, w)
            elif de_type == "6_stairs":
                h = de[2]
                dx = de[3]  # -1 or 1
                if choice < 0.33:
                    x = offset_by_upto(x, width / 8, min=1, max=width - 2)
                elif choice < 0.66:
                    h = offset_by_upto(h, 8, min=1, max=9)
                else:
                    dx = -dx
                new_de = (x, de_type, h, dx)
            elif de_type == "1_platform":
                w = de[2]
                y = de[3]
                madeof = de[4]  # from "?", "X", "B"
                if choice < 0.25:
                    x = offset_by_upto(x, width / 8, min=1, max=width - 2)
                elif choice < 0.5:
                    w = offset_by_upto(w, 8, min=1, max=width - 2)
                elif choice < 0.75:
                    y = offset_by_upto(y, height, min=0, max=height - 1)
                else:
                    madeof = random.choice(["?", "X", "B"])
                new_de = (x, de_type, w, y, madeof)
            elif de_type == "2_enemy":
                x = offset_by_upto(x, width / 8, min=1, max=width - 2)
                new_de = (x, de_type)
            new_genome.pop(to_change)
            heapq.heappush(new_genome, new_de)
        return new_genome

    def generate_children(self, other):
        # STUDENT How does this work?  Explain it in your writeup.
        pa = random.randint(0, len(self.genome) - 1) if len(self.genome) > 0 else 0  # if len(self.genome) > 0 else 0 added to fix bug
        pb = random.randint(0, len(other.genome) - 1) if len(other.genome) > 0 else 0  # if len(other.genome) > 0 else 0 added to fix bug
        a_part = self.genome[:pa] if len(self.genome) > 0 else []
        b_part = other.genome[pb:] if len(other.genome) > 0 else []
        ga = a_part + b_part
        b_part = other.genome[:pb] if len(other.genome) > 0 else []
        a_part = self.genome[pa:] if len(self.genome) > 0 else []
        gb = b_part + a_part
        # do mutation
        return Individual_DE(self.mutate(ga)), Individual_DE(self.mutate(gb))

    # Apply the DEs to a base level.
    def to_level(self):
        if self._level is None:
            base = Individual_Grid.empty_individual().to_level()
            for de in sorted(self.genome, key=lambda de: (de[1], de[0], de)):
                # de: x, type, ...
                x = de[0]
                de_type = de[1]
                if de_type == "4_block":
                    y = de[2]
                    breakable = de[3]
                    base[y][x] = "B" if breakable else "X"
                elif de_type == "5_qblock":
                    y = de[2]
                    has_powerup = de[3]  # boolean
                    base[y][x] = "M" if has_powerup else "?"
                elif de_type == "3_coin":
                    y = de[2]
                    base[y][x] = "o"
                elif de_type == "7_pipe":
                    h = de[2]
                    base[height - h - 1][x] = "T"
                    for y in range(height - h, height):
                        base[y][x] = "|"
                elif de_type == "0_hole":
                    w = de[2]
                    for x2 in range(w):
                        base[height - 1][clip(1, x + x2, width - 2)] = "-"
                elif de_type == "6_stairs":
                    h = de[2]
                    dx = de[3]  # -1 or 1
                    for x2 in range(1, h + 1):
                        for y in range(x2 if dx == 1 else h - x2):
                            base[clip(0, height - y - 1, height - 1)][clip(1, x + x2, width - 2)] = "X"
                elif de_type == "1_platform":
                    w = de[2]
                    h = de[3]
                    madeof = de[4]  # from "?", "X", "B"
                    for x2 in range(w):
                        base[clip(0, height - h - 1, height - 1)][clip(1, x + x2, width - 2)] = madeof
                elif de_type == "2_enemy":
                    base[height - 2][x] = "E"
            self._level = base
        return self._level

    @classmethod
    def empty_individual(_cls):
        # STUDENT Maybe enhance this
        g = []
        return Individual_DE(g)

    @classmethod
    def random_individual(_cls):
        # STUDENT Maybe enhance this
        elt_count = random.randint(8, 128)
        g = [random.choice([
            (random.randint(1, width - 2), "0_hole", random.randint(1, 8)),
            (random.randint(1, width - 2), "1_platform", random.randint(1, 8), random.randint(0, height - 1), random.choice(["?", "X", "B"])),
            (random.randint(1, width - 2), "2_enemy"),
            (random.randint(1, width - 2), "3_coin", random.randint(0, height - 1)),
            (random.randint(1, width - 2), "4_block", random.randint(0, height - 1), random.choice([True, False])),
            (random.randint(1, width - 2), "5_qblock", random.randint(0, height - 1), random.choice([True, False])),
            (random.randint(1, width - 2), "6_stairs", random.randint(1, 9), random.choice([-1, 1])),
            (random.randint(1, width - 2), "7_pipe", random.randint(2, 9))
        ]) for i in range(elt_count)]
        return Individual_DE(g)


Individual = Individual_Grid

def tournament_select(population):
    sample = random.sample(population, 10)
    winner = max(sample, key=lambda individual: individual.fitness())
    return winner

def elites_select(population, count):
    sorted_population = sorted(population, key=lambda individual: individual.fitness(), reverse=True) #reverse so better ones at front
    return sorted_population[:count]

def roulette_select(population):
    fitnesses = [individual.fitness() for individual in population]
    minimum = min(fitnesses) - 0.01 # shift to avoid individual with minimum fitness get weight of 0
    weights = []
    for fitness in fitnesses:
        weight = (fitness - minimum)
        weights.append(weight)
    return random.choices(population, weights=weights, k=1)[0]

def generate_successors(population):
    results = []
    # STUDENT Design and implement this
    # Hint: Call generate_children() on some individuals and fill up results.
    limit_size = len(population)

    results = elites_select(population, 20) # can always change the count

    while len(results) < limit_size:
        if SELECTION_METHOD == "tournament":
            # parent A -> strategy
            parentA = tournament_select(population)
            # parent B -> strategy
            parentB = tournament_select(population)
        else:
            parentA = roulette_select(population)
            parentB = roulette_select(population)

        children = parentA.generate_children(parentB)

        for child in children:
            if len(results) >= limit_size:
                break
            results.append(child)

    print("Population:", len(population), "->", len(results))

    return results


def ga():
    # STUDENT Feel free to play with this parameter
    pop_limit = 480
    # Code to parallelize some computations
    batches = os.cpu_count()
    if pop_limit % batches != 0:
        print("It's ideal if pop_limit divides evenly into " + str(batches) + " batches.")
    batch_size = int(math.ceil(pop_limit / batches))
    with mpool.Pool(processes=os.cpu_count()) as pool:
        init_time = time.time()
        # STUDENT (Optional) change population initialization
        population = [Individual.random_individual() if random.random() < 0.9
                      else Individual.empty_individual()
                      for _g in range(pop_limit)]
        # But leave this line alone; we have to reassign to population because we get a new population that has more cached stuff in it.
        population = pool.map(Individual.calculate_fitness,
                              population,
                              batch_size)
        init_done = time.time()
        print("Created and calculated initial population statistics in:", init_done - init_time, "seconds")
        generation = 0
        start = time.time()
        now = start
        print("Use ctrl-c to terminate this loop manually.")
        try:
            while True:
                now = time.time()
                # Print out statistics
                if generation > 0:
                    best = max(population, key=Individual.fitness)
                    print("Generation:", str(generation))
                    print("Max fitness:", str(best.fitness()))
                    print("Average generation time:", (now - start) / generation)
                    print("Net time:", now - start)
                    with open("levels/last.txt", 'w') as f:
                        for row in best.to_level():
                            f.write("".join(row) + "\n")
                generation += 1
                # STUDENT Determine stopping condition
                stop_condition = False
                if stop_condition:
                    break
                # STUDENT Also consider using FI-2POP as in the Sorenson & Pasquier paper
                gentime = time.time()
                next_population = generate_successors(population)
                gendone = time.time()
                print("Generated successors in:", gendone - gentime, "seconds")
                # Calculate fitness in batches in parallel
                next_population = pool.map(Individual.calculate_fitness,
                                           next_population,
                                           batch_size)
                popdone = time.time()
                print("Calculated fitnesses in:", popdone - gendone, "seconds")
                population = next_population
        except KeyboardInterrupt:
            pass
    return population


if __name__ == "__main__":
    final_gen = sorted(ga(), key=Individual.fitness, reverse=True)
    best = final_gen[0]
    print("Best fitness: " + str(best.fitness()))
    now = time.strftime("%m_%d_%H_%M_%S")
    # STUDENT You can change this if you want to blast out the whole generation, or ten random samples, or...
    for k in range(0, 10):
        with open("levels/" + now + "_" + str(k) + ".txt", 'w') as f:
            for row in final_gen[k].to_level():
                f.write("".join(row) + "\n")
