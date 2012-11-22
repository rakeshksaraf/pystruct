import itertools
import numpy as np

from pyqpbo import alpha_expansion_grid
from pyqpbo import alpha_expansion_graph
from daimrf import mrf
from lp_new import solve_lp


from IPython.core.debugger import Tracer
tracer = Tracer()


class StructuredProblem(object):
    def __init__(self):
        self.size_psi = None
        self.inference_calls = 0

    def psi(self, x, y):
        pass

    def inference(self, x, w):
        pass

    def loss(self, y, y_hat):
        # hamming loss:
        return np.sum(y != y_hat)

    def loss_augmented_inference(self, x, y, w):
        print("FALLBACK no loss augmented inference found")
        return self.inference(x, w)


class GridCRF(StructuredProblem):
    def __init__(self, n_states=2, inference_method='qpbo'):
        super(GridCRF, self).__init__()
        self.n_states = n_states
        self.inference_method = inference_method
        # n_states unary parameters, upper triangular for pairwise
        self.size_psi = n_states + n_states * (n_states + 1) / 2

    def psi(self, x, y):
        # x is unaries
        # y is a labeling
        ## y can also be continuous (from lp)
        if x.shape == y.shape:
            x_flat = x.reshape(-1, x.shape[-1])
            y_flat = y.reshape(-1, y.shape[-1])
            unaries_acc = np.sum(x_flat * y_flat, axis=0)
            labels = y
        else:
            ## unary features:
            gx, gy = np.ogrid[:x.shape[0], :x.shape[1]]
            selected_unaries = x[gx, gy, y]
            unaries_acc = np.bincount(y.ravel(), selected_unaries.ravel(),
                                      minlength=self.n_states)

            ##accumulated pairwise
            #make one hot encoding
            labels = np.zeros((y.shape[0], y.shape[1], self.n_states),
                              dtype=np.int)
            labels[gx, gy, y] = 1

        # vertical edges
        vert = np.dot(labels[1:, :, :].reshape(-1, self.n_states).T,
                      labels[:-1, :, :].reshape(-1, self.n_states))
        # horizontal edges
        horz = np.dot(labels[:, 1:, :].reshape(-1, self.n_states).T,
                      labels[:, :-1, :].reshape(-1, self.n_states))
        pw = vert + horz
        pw = pw + pw.T - np.diag(np.diag(pw))
        feature = np.hstack([unaries_acc,
                             pw[np.tri(self.n_states, dtype=np.bool)]])
        return feature

    def inference(self, x, w, relaxed=False):
        self.inference_calls += 1
        if w.shape != (self.size_psi,):
            raise ValueError("Got w of wrong shape. Expected %s, got %s" %
                             (self.size_psi, w.shape))
        unary_params = w[:self.n_states]
        pairwise_flat = np.asarray(w[self.n_states:])
        pairwise_params = np.zeros((self.n_states, self.n_states))
        pairwise_params[np.tri(self.n_states, dtype=np.bool)] = pairwise_flat
        pairwise_params = (pairwise_params + pairwise_params.T
                           - np.diag(np.diag(pairwise_params)))
        if self.inference_method == "qpbo":
            return self._inference_qpbo(x, unary_params, pairwise_params)
        elif  self.inference_method == "dai":
            return self._inference_dai(x, unary_params, pairwise_params)
        elif  self.inference_method == "lp":
            return self._inference_lp(x, unary_params, pairwise_params,
                                      relaxed)
        else:
            raise ValueError("inference_method must be 'qpbo' or 'dai', got %s"
                             % self.inference_method)

    def _inference_qpbo(self, x, unary_params, pairwise_params):
        unaries = (-1000 * unary_params * x).astype(np.int32)
        pairwise = (-1000 * pairwise_params).astype(np.int32)
        y = alpha_expansion_grid(unaries, pairwise)
        return y

    def _inference_dai(self, x, unary_params, pairwise_params):
        ## build graph
        inds = np.arange(x.shape[0] * x.shape[1]).reshape(x.shape[:2])
        inds = inds.astype(np.int64)
        horz = np.c_[inds[:, :-1].ravel(), inds[:, 1:].ravel()]
        vert = np.c_[inds[:-1, :].ravel(), inds[1:, :].ravel()]
        edges = np.vstack([horz, vert])
        log_unaries = unary_params * x.reshape(-1, self.n_states)
        max_entry = max(np.max(log_unaries), 1)
        unaries = np.exp(log_unaries / max_entry)

        y = mrf(unaries, edges, np.exp(pairwise_params / max_entry), alg='jt')
        y = y.reshape(x.shape[0], x.shape[1])

        return y

    def _inference_lp(self, x, unary_params, pairwise_params, relaxed=False):
        ## build graph
        inds = np.arange(x.shape[0] * x.shape[1]).reshape(x.shape[:2])
        inds = inds.astype(np.int64)
        horz = np.c_[inds[:, :-1].ravel(), inds[:, 1:].ravel()]
        vert = np.c_[inds[:-1, :].ravel(), inds[1:, :].ravel()]
        edges = np.vstack([horz, vert])
        unaries = unary_params * x.reshape(-1, self.n_states)
        y = solve_lp(-unaries, edges, -pairwise_params)
        n_fractional = np.sum(y.max(axis=-1) < .9)
        if n_fractional:
            print("fractional solutions found: %d" % n_fractional)
        if relaxed:
            return y.reshape(x.shape)
        y = np.argmax(y, axis=-1)
        y = y.reshape(x.shape[0], x.shape[1])
        return y

    def loss_augmented_inference(self, x, y, w, relaxed=False):
        if w.shape != (self.size_psi,):
            raise ValueError("Got w of wrong shape. Expected %s, got %s" %
                             (self.size_psi, w.shape))
        unary_params = w[:self.n_states]
        # avoid division by zero:
        unary_params[unary_params == 0] = 1e-10
        x_ = x.copy()
        for l in np.arange(self.n_states):
            # for each class, decrement unaries
            # for loss-agumention
            x_[y != l, l] += 1. / unary_params[l]
        return self.inference(x_, w, relaxed)


class FixedGraphCRF(StructuredProblem):
    """CRF with general graph that is THE SAME for all examples.
    graph is given by scipy sparse adjacency matrix.
    """
    def __init__(self, n_states, graph):
        self.n_states = n_states
        # n_states unary parameters, upper triangular for pairwise
        self.size_psi = n_states + n_states * (n_states + 1) / 2
        self.graph = graph
        self.edges = np.c_[graph.nonzero()].copy("C")

    def psi(self, x, y):
        # x is unaries
        # y is a labeling
        ## unary features:
        n_nodes = y.shape[0]
        gx = np.ogrid[:n_nodes]
        selected_unaries = x[gx, y]
        unaries_acc = np.bincount(y.ravel(), selected_unaries.ravel(),
                                  minlength=self.n_states)

        ##accumulated pairwise
        #make one hot encoding
        labels = np.zeros((n_nodes, self.n_states),
                          dtype=np.int)
        gx = np.ogrid[:n_nodes]
        labels[gx, y] = 1

        neighbors = self.graph * labels
        pw = np.dot(neighbors.T, labels)

        feature = np.hstack([unaries_acc,
                             pw[np.tri(self.n_states, dtype=np.bool)]])
        return feature

    def inference(self, x, w):
        if w.shape != (self.size_psi,):
            raise ValueError("Got w of wrong shape. Expected %s, got %s" %
                             (self.size_psi, w.shape))
        self.inference_calls += 1
        unary_params = w[:self.n_states]
        pairwise_flat = np.asarray(w[self.n_states:])
        pairwise_params = np.zeros((self.n_states, self.n_states))
        pairwise_params[np.tri(self.n_states, dtype=np.bool)] = pairwise_flat
        pairwise_params = (pairwise_params + pairwise_params.T
                           - np.diag(np.diag(pairwise_params)))
        unaries = (-1000 * unary_params * x).astype(np.int32)
        pairwise = (-1000 * pairwise_params).astype(np.int32)
        y = alpha_expansion_graph(self.edges, unaries, pairwise, random_seed=1)
        return y

    def loss_augmented_inference(self, x, y, w):
        if w.shape != (self.size_psi,):
            raise ValueError("Got w of wrong shape. Expected %s, got %s" %
                             (self.size_psi, w.shape))
        unary_params = w[:self.n_states].copy()
        # avoid division by zero:
        unary_params[unary_params == 0] = 1e-10
        x_ = x.copy()
        for l in np.arange(self.n_states):
            # for each class, decrement unaries
            # for loss-agumention
            x_[y != l, l] += 1. / unary_params[l]
        return self.inference(x_, w)


class FixedGraphCRFNoBias(GridCRF):
    """CRF with general graph that is THE SAME for all examples.
    graph is given by scipy sparse adjacency matrix.
    """
    def __init__(self, n_states, graph):
        self.inference_calls = 0
        self.n_states = n_states
        #upper triangular for pairwise
        # last one gives weights to unary potentials
        self.size_psi = n_states * (n_states - 1) / 2 + 1
        self.graph = graph
        self.edges = np.c_[graph.nonzero()].copy("C")

    def psi(self, x, y):
        # x is unaries
        # y is a labeling
        ## unary features:
        n_nodes = y.shape[0]
        gx = np.ogrid[:n_nodes]
        unaries_acc = x[gx, y].sum()

        # x is unaries
        # y is a labeling
        n_nodes = y.shape[0]

        ##accumulated pairwise
        #make one hot encoding
        labels = np.zeros((n_nodes, self.n_states), dtype=np.int)
        gx = np.ogrid[:n_nodes]
        labels[gx, y] = 1

        neighbors = self.graph * labels
        pw = np.dot(neighbors.T, labels)

        pairwise = pw[np.tri(self.n_states, k=-1, dtype=np.bool)]
        feature = np.hstack([unaries_acc, pairwise])
        return feature

    def inference(self, x, w):
        if w.shape != (self.size_psi,):
            raise ValueError("Got w of wrong shape. Expected %s, got %s" %
                             (self.size_psi, w.shape))
        self.inference_calls += 1
        pairwise_flat = np.asarray(w[:-1])
        unary = w[-1]
        pairwise_params = np.zeros((self.n_states, self.n_states))
        upper_tri = np.tri(self.n_states, k=-1, dtype=np.bool)
        pairwise_params[upper_tri] = pairwise_flat
        pairwise_params = (pairwise_params + pairwise_params.T
                           - np.diag(np.diag(pairwise_params)))
        unaries = (-1000 * unary * x).astype(np.int32)
        pairwise = (-1000 * pairwise_params).astype(np.int32)
        y = alpha_expansion_graph(self.edges, unaries, pairwise, random_seed=1)
        from pyqpbo import binary_graph
        y_ = binary_graph(self.edges, unaries, pairwise)
        if (y_ != y).any():
            tracer()
        return y

    def loss_augmented_inference(self, x, y, w):
        if w.shape != (self.size_psi,):
            raise ValueError("Got w of wrong shape. Expected %s, got %s" %
                             (self.size_psi, w.shape))
        x_ = x.copy()
        for l in np.arange(self.n_states):
            # for each class, decrement unaries
            # for loss-agumention
            x_[y != l, l] += 1.
        return self.inference(x_, w)


def exhaustive_loss_augmented_inference(problem, x, y, w):
    size = np.prod(x.shape[:-1])
    best_y = None
    best_energy = np.inf
    for y_hat in itertools.product(range(problem.n_states), repeat=size):
        y_hat = np.array(y_hat).reshape(x.shape[:-1])
        print("trying %s" % repr(y_hat))
        psi = problem.psi(x, y_hat)
        energy = -problem.loss(y, y_hat) - np.dot(w, psi)
        if energy < best_energy:
            best_energy = energy
            best_y = y_hat
    return best_y
