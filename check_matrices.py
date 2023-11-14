import numba
import pickle
import time
import itertools as it
import multiprocessing as mp
import numpy as np

from helper_functions import rref_binary
from typing import Iterator, List, Sequence, Tuple


def np_block(X):
    xtmp1 = np.hstack(X[0])
    xtmp2 = np.hstack(X[1])
    return np.vstack((xtmp1, xtmp2))


# ***** EDIT THIS BEFORE RUNNING *****
n = 6

O = np.zeros((n, n), dtype=np.int8)
I = np.eye(n, dtype=np.int8)
Lambda = np_block(((O, I), (-I, O)))


def powerset(iterable) -> Iterator:
    "powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
    s = list(iterable)
    return it.chain.from_iterable(it.combinations(s, r) for r in range(len(s)+1))


def generate_rref_matrices(shape: Tuple, leading_one_positions: Sequence[int]) -> Iterator[np.ndarray]:
    template = np.zeros(shape, dtype=np.int8)
    # template = np.zeros((n, 2*n), dtype=np.int8)

    valid_positions = []

    for i, pos in enumerate(leading_one_positions):
        template[i, pos] = 1
        vps = set(range(pos + 1, shape[1])).difference(leading_one_positions)
        valid_positions.append(vps)

    all_combinations = (powerset(vps) for vps in valid_positions)
    for choice in it.product(*all_combinations):
        matrix = np.copy(template)
        for i, positions in enumerate(choice):
            matrix[i, positions] = 1
        yield matrix


def dot_py(A, B):
    m, n = A.shape
    p = B.shape[1]

    C = np.zeros((m, p), dtype=np.int8)

    for i in range(0, m):
        for j in range(0, p):
            for k in range(0, n):
                C[i, j] += A[i, k] * B[k, j]
    return C


dot_nb = numba.jit(numba.int8[:, :](
    numba.int8[:, :], numba.int8[:, :]), nopython=True)(dot_py)


def check_commute(check_matrix):
    """
    Checks whether the generators, given by the check matrix
    check_matrix, commute with each other.

    We use the fact that the generators commute if and only if

    .. math::
        G \Lambda G^T = 0 \pmod{2},

    where :math:`G` is the matrix whose rows are check vectors
    (i.e. check_matrix) and

    .. math::
        \Lambda = \\begin{pmatrix} 0 & I \\\ I & 0 \end{pmatrix}.

    Parameters
    ----------
    check_matrix : np.ndarray

    Returns
    -------
    commute : bool
        Whether or not the generators commute.

    """

    intermediate = dot_nb(check_matrix, Lambda)
    prod = dot_nb(intermediate, check_matrix.T)

    return np.array_equiv(prod % 2, O)


def rref_wrapper(leading_one_positions):
    """

    Parameters
    ----------
    leading_one_positions : Sequence[int]

    Returns
    -------
    good_ones : List[np.ndarray]

    """

    print(f'Begin looking with {leading_one_positions = }')

    good_ones = []

    for index, mat in enumerate(
            generate_rref_matrices((n, n), leading_one_positions)):
        good_ones.append(mat)

    print(f'Finish looking with {leading_one_positions = }')

    return good_ones


def get_top_left():
    start_time = time.perf_counter()

    top_lefts = []

    with mp.Pool() as pool, \
            open(f'data/{n}_qubit_top_left.data', 'ab') as writer:
        results = pool.imap(
            rref_wrapper,
            powerset(range(n)),
            chunksize=10
        )

        for sublist in results:
            top_lefts += sublist
            if len(top_lefts) > 100_000:
                pickle.dump(top_lefts, writer)
                top_lefts = []

        pickle.dump(top_lefts, writer)

    print(f'Total elapsed time: {time.perf_counter() - start_time}')

    return top_lefts


def get_bottom_right_and_merge(top_lefts: Sequence[np.ndarray]):
    merged_mats = []

    for tl in top_lefts:
        tl = tl[~np.all(tl == 0, axis=1)]
        br = np.zeros((n - tl.shape[0], n), dtype=np.int8)

        # Find pivot columns
        pivots = np.argmax(tl, axis=1).tolist()

        # Put 1's in the correct positions
        i = 0
        for j in range(n):
            if j not in pivots:
                br[i, j] = 1
                br[i, pivots] = tl[:, j]
                i += 1

        mat = np.zeros((n, 2*n), dtype=np.int8)
        mat[:tl.shape[0], :n] = tl
        mat[tl.shape[0]:, n:] = br
        # Also save the shape of the top right block and
        # the locations of the non-pivots for the right blocks
        merged_mats.append((mat, (tl.shape[0], n), pivots))

    return merged_mats


def generate_top_right(tl: np.ndarray, nonzero_cols: List,
                       temp=None, next_row_index=0):
    if temp is None:
        print(f'Begin generation for\n{tl = }\n{nonzero_cols = }\n')
        temp = np.zeros(tl.shape, dtype=np.int8)

    for cand_row_nzs in it.product((0, 1), repeat=len(nonzero_cols)):
        cand_row = np.zeros(n, dtype=np.int8)
        cand_row[nonzero_cols] = cand_row_nzs
        if all(np.array_equiv(np.dot(tl[i, :], cand_row) % 2,
                              np.dot(tl[next_row_index, :], temp[i, :]) % 2)
               for i in range(next_row_index)):
            temp[next_row_index, :] = cand_row
            if next_row_index == tl.shape[0] - 1:
                yield np.copy(temp)
            else:
                yield from generate_top_right(tl, nonzero_cols, np.copy(temp),
                                              next_row_index + 1)


def top_right_wrapper(args):
    mat, shape, pivots = args
    finished_mats = []
    for tr in generate_top_right(mat[:shape[0], :shape[1]], pivots):
        xmatr = np.copy(mat)
        xmatr[:shape[0], n:] = tr
        finished_mats.append(xmatr)
    return finished_mats


def generate_top_right_full_support(temp=np.zeros((n, n), dtype=np.int8),
                                    next_row_index=0):
    num_unfixed_cols = n - next_row_index
    cand_row = np.zeros(n, dtype=np.int8)

    for j in range(next_row_index):
        cand_row[j] = temp[j, next_row_index]

    for cand_row_rest in it.product((0, 1), repeat=num_unfixed_cols):
        cand_row[-num_unfixed_cols:] = cand_row_rest
        temp[next_row_index, :] = cand_row
        if next_row_index == n - 1:
            yield np.copy(temp)
        else:
            yield from generate_top_right_full_support(np.copy(temp),
                                                       next_row_index + 1)


def finish(merged_mats: Sequence[Tuple[np.ndarray, Tuple, List]]):
    start_time = time.perf_counter()

    xmatrs = []

    with mp.Pool() as pool, \
            open(f'data/{n}_qubit_subgroups_cool.data', 'ab') as writer:
        xmatrs.append(merged_mats[0][0])

        results = pool.imap_unordered(top_right_wrapper,
                                      merged_mats[1:-1],
                                      chunksize=10)

        for sublist in results:
            xmatrs += sublist
            if len(xmatrs) > 100_000:
                pickle.dump(xmatrs, writer)
                xmatrs = []

        pickle.dump(xmatrs, writer)
        xmatrs = []

        # Now deal with the last case (support is whole of F_2^n,
        # and the X part of the check matrix is the identity matrix)
        for right in generate_top_right_full_support():
            xmatr = np.copy(merged_mats[-1][0])
            xmatr[:, n:] = right
            xmatrs.append(xmatr)
            if len(xmatrs) > 100_000:
                pickle.dump(xmatrs, writer)
                xmatrs = []

        pickle.dump(xmatrs, writer)

    print(f'Total elapsed time: {time.perf_counter() - start_time}')

    return xmatrs


def polish(xmatrs: List[np.ndarray]):
    print('Polishing step 1: row reduce every matrix')
    # Put all matrices in rref
    for i, xmatr in enumerate(xmatrs):
        xmatrs[i] = rref_binary(xmatr)

    print('Polishing step 2: sort in increasing order of support size')
    # Order by support size
    xmatrs.sort(key=lambda mat:
                np.count_nonzero(~np.all(mat[:, :n] == 0, axis=1)))
    return xmatrs


if __name__ == '__main__':
    # with open(f'data/{n}_qubit_top_left.data', 'rb') as reader:
    #     top_lefts = pickle.load(reader)
    # merged_mats = get_bottom_right_and_merge(top_lefts)
    # print(len(merged_mats))
    # last_xmatrs = finish(merged_mats)

    unpolished_xmatrs = []
    with open(f'data/{n}_qubit_subgroups_cool.data', 'rb') as reader:
        try:
            while True:
                unpolished_xmatrs += pickle.load(reader)
        except EOFError:
            pass

    unpolished_xmatrs[0] = unpolished_xmatrs[0][0]
    polished_xmatrs = polish(unpolished_xmatrs)

    with open(f'data/{n}_qubit_subgroups_cool.data', 'wb') as writer:
        pickle.dump(polished_xmatrs, writer)
