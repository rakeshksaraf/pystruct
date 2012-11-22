import numpy as np
import matplotlib.pyplot as plt

from latent_crf import LatentGridCRF
from latent_structured_svm import StupidLatentSVM

import toy_datasets as toy

from IPython.core.debugger import Tracer
tracer = Tracer()


def main():
    X, Y = toy.generate_crosses_latent(n_samples=25, noise=10)
    #X, Y = toy.generate_big_checker(n_samples=25, noise=.5)
    n_labels = 2
    #crf = LatentGridCRF(n_labels=n_labels, n_states_per_label=2,
                        #inference_method='dai')
    crf = LatentGridCRF(n_labels=n_labels, n_states_per_label=2,
                        inference_method='lp')
    clf = StupidLatentSVM(problem=crf, max_iter=50, C=10000, verbose=2,
                          check_constraints=True, n_jobs=12)
    #clf = StupidLatentSVM(problem=crf, max_iter=50, C=1, verbose=2,
            #check_constraints=True, n_jobs=12)
    clf.fit(X, Y)
    Y_pred = clf.predict(X)

    i = 0
    loss = 0
    for x, y, y_pred in zip(X, Y, Y_pred):
        y_pred = y_pred.reshape(x.shape[:2])
        loss += np.sum(y / 2 != y_pred / 2)
        if i > 20:
            continue
        plt.subplot(131)
        plt.imshow(y, interpolation='nearest')
        plt.colorbar()
        plt.subplot(132)
        w_unaries_only = np.array([1, 1, 1, 1,
                                   0,
                                   0, 0,
                                   0, 0, 0,
                                   0, 0, 0, 0])
        unary_pred = crf.inference(x, w_unaries_only)
        plt.imshow(unary_pred, interpolation='nearest')
        plt.colorbar()
        plt.subplot(133)
        plt.imshow(y_pred, interpolation='nearest')
        plt.colorbar()
        plt.savefig("data_%03d.png" % i)
        plt.close()
        i += 1
    print("loss: %f" % loss)

if __name__ == "__main__":
    main()
