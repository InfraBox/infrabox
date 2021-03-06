package main

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"time"

	b64 "encoding/base64"
	"github.com/golang/glog"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/runtime"
	"k8s.io/apimachinery/pkg/util/wait"
	kubeinformers "k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/kubernetes/scheme"
	typedcorev1 "k8s.io/client-go/kubernetes/typed/core/v1"
	corelisters "k8s.io/client-go/listers/core/v1"
	"k8s.io/client-go/tools/cache"
	"k8s.io/client-go/tools/record"
	"k8s.io/client-go/util/workqueue"

	clusterv1alpha1 "github.com/infrabox/infrabox/src/services/gcp/pkg/apis/gcp/v1alpha1"
	clientset "github.com/infrabox/infrabox/src/services/gcp/pkg/client/clientset/versioned"
	gkescheme "github.com/infrabox/infrabox/src/services/gcp/pkg/client/clientset/versioned/scheme"
	informers "github.com/infrabox/infrabox/src/services/gcp/pkg/client/informers/externalversions"
	listers "github.com/infrabox/infrabox/src/services/gcp/pkg/client/listers/gcp/v1alpha1"
)

const controllerAgentName = "infrabox-service-gcp"

type Controller struct {
	kubeclientset  kubernetes.Interface
	gkeclientset   clientset.Interface
	clusterLister  listers.GKEClusterLister
	clustersSynced cache.InformerSynced
	secretsLister  corelisters.SecretLister
	secretsSynced  cache.InformerSynced
	workqueue      workqueue.RateLimitingInterface
	recorder       record.EventRecorder
}

// NewController returns a new sample controller
func NewController(
	kubeclientset kubernetes.Interface,
	gkeclientset clientset.Interface,
	kubeInformerFactory kubeinformers.SharedInformerFactory,
	gkeInformerFactory informers.SharedInformerFactory) *Controller {

	clusterInformer := gkeInformerFactory.Gcp().V1alpha1().GKEClusters()
	secretsInformer := kubeInformerFactory.Core().V1().Secrets()

	// Create event broadcaster
	// Add sample-controller types to the default Kubernetes Scheme so Events can be
	// logged for sample-controller types.
	gkescheme.AddToScheme(scheme.Scheme)
	glog.V(4).Info("Creating event broadcaster")
	eventBroadcaster := record.NewBroadcaster()
	eventBroadcaster.StartLogging(glog.Infof)
	eventBroadcaster.StartRecordingToSink(&typedcorev1.EventSinkImpl{Interface: kubeclientset.CoreV1().Events("")})
	recorder := eventBroadcaster.NewRecorder(scheme.Scheme, corev1.EventSource{Component: controllerAgentName})

	controller := &Controller{
		kubeclientset:  kubeclientset,
		gkeclientset:   gkeclientset,
		clusterLister:  clusterInformer.Lister(),
		clustersSynced: clusterInformer.Informer().HasSynced,
		secretsLister:  secretsInformer.Lister(),
		secretsSynced:  secretsInformer.Informer().HasSynced,
		workqueue:      workqueue.NewNamedRateLimitingQueue(workqueue.DefaultControllerRateLimiter(), "GKEClusters"),
		recorder:       recorder,
	}

	glog.Info("Setting up event handlers")

	clusterInformer.Informer().AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc: controller.enqueueCluster,
		UpdateFunc: func(old, new interface{}) {
			controller.enqueueCluster(new)
		},
		DeleteFunc: func(old interface{}) {},
	})

	return controller
}

func (c *Controller) Run(threadiness int, stopCh <-chan struct{}) error {
	defer runtime.HandleCrash()
	defer c.workqueue.ShutDown()

	glog.Info("Starting Cluster controller")

	glog.Info("Waiting for informer caches to sync")
	if ok := cache.WaitForCacheSync(stopCh, c.clustersSynced); !ok {
		return fmt.Errorf("failed to wait for caches to sync")
	}

	glog.Info("Starting workers")
	for i := 0; i < threadiness; i++ {
		go wait.Until(c.runWorker, time.Second, stopCh)
	}

	glog.Info("Started workers")
	<-stopCh
	glog.Info("Shutting down workers")

	return nil
}

func (c *Controller) runWorker() {
	for c.processNextWorkItem() {
	}
}

func (c *Controller) processNextWorkItem() bool {
	obj, shutdown := c.workqueue.Get()

	if shutdown {
		return false
	}

	err := func(obj interface{}) error {
		defer c.workqueue.Done(obj)
		var key string
		var ok bool

		if key, ok = obj.(string); !ok {
			c.workqueue.Forget(obj)
			runtime.HandleError(fmt.Errorf("expected string in workqueue but got %#v", obj))
			return nil
		}

		if err := c.syncHandler(key); err != nil {
			return fmt.Errorf("%s: error syncing: %s", key, err.Error())
		}

		c.workqueue.Forget(obj)
		return nil
	}(obj)

	if err != nil {
		runtime.HandleError(err)
		return true
	}

	return true
}

type MasterAuth struct {
	ClientCertificate    string
	ClientKey            string
	ClusterCaCertificate string
	Username             string
	Password             string
}

type RemoteCluster struct {
	Name       string
	Status     string
	Endpoint   string
	MasterAuth MasterAuth
}

func (c *Controller) updateClusterStatus(cluster *clusterv1alpha1.GKECluster, gke *RemoteCluster) error {
	oldStatus := cluster.Status.Status

	switch gke.Status {
	case "RUNNING":
		cluster.Status.Status = "ready"
	case "PROVISIONING":
		cluster.Status.Status = "pending"
	default:
		cluster.Status.Status = "error"
	}

	if cluster.Status.Status == oldStatus {
		return nil
	}

	_, err := c.gkeclientset.GcpV1alpha1().GKEClusters(cluster.Namespace).Update(cluster)
	return err
}

func (c *Controller) getRemoteClusters() ([]RemoteCluster, error) {
	cmd := exec.Command("gcloud", "container", "clusters", "list", "--format", "json")
	out, err := cmd.CombinedOutput()

	if err != nil {
		runtime.HandleError(fmt.Errorf("Could not list clusters: %s", err.Error()))
		return nil, err
	}

	var gkeclusters []RemoteCluster
	err = json.Unmarshal(out, &gkeclusters)

	if err != nil {
		runtime.HandleError(fmt.Errorf("Could not parse cluster list: %s", err.Error()))
		return nil, err
	}

	return gkeclusters, nil
}

func (c *Controller) getRemoteCluster(name string) (*RemoteCluster, error) {
	cmd := exec.Command("gcloud", "container", "clusters", "list",
		"--filter", "name=ib-"+name, "--format", "json")

	out, err := cmd.CombinedOutput()

	if err != nil {
		runtime.HandleError(fmt.Errorf("Could not list clusters: %s", err.Error()))
		glog.Warning(string(out))
		return nil, err
	}

	var gkeclusters []RemoteCluster
	err = json.Unmarshal(out, &gkeclusters)

	if err != nil {
		runtime.HandleError(fmt.Errorf("Could not parse cluster list: %s", err.Error()))
		glog.Warning(string(out))
		return nil, err
	}

	if len(gkeclusters) == 0 {
		return nil, nil
	}

	return &gkeclusters[0], nil
}

func newSecret(cluster *clusterv1alpha1.GKECluster, gke *RemoteCluster) *corev1.Secret {
	caCrt, _ := b64.StdEncoding.DecodeString(gke.MasterAuth.ClusterCaCertificate)
	clientKey, _ := b64.StdEncoding.DecodeString(gke.MasterAuth.ClientKey)
	clientCrt, _ := b64.StdEncoding.DecodeString(gke.MasterAuth.ClientCertificate)

	secretName := cluster.ObjectMeta.Labels["service.infrabox.net/secret-name"]

	return &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      secretName,
			Namespace: cluster.Namespace,
			OwnerReferences: []metav1.OwnerReference{
				*metav1.NewControllerRef(cluster, schema.GroupVersionKind{
					Group:   clusterv1alpha1.SchemeGroupVersion.Group,
					Version: clusterv1alpha1.SchemeGroupVersion.Version,
					Kind:    "Cluster",
				}),
			},
		},
		Type: "Opaque",
		Data: map[string][]byte{
			"ca.crt":     []byte(caCrt),
			"client.key": []byte(clientKey),
			"client.crt": []byte(clientCrt),
			"username":   []byte(gke.MasterAuth.Username),
			"password":   []byte(gke.MasterAuth.Password),
			"endpoint":   []byte("https://" + gke.Endpoint),
		},
	}
}

func (c *Controller) deleteSecret(cluster *clusterv1alpha1.GKECluster) (bool, error) {
	secretName := cluster.ObjectMeta.Labels["service.infrabox.net/secret-name"]
	secret, err := c.secretsLister.Secrets(cluster.Namespace).Get(secretName)

	if err != nil {
		if errors.IsNotFound(err) {
			return true, nil
		}

		return false, err
	}

	if secret != nil {
		return true, nil
	}

	glog.Infof("%s/%s: Deleting secret for cluster credentials", cluster.Namespace, cluster.Name)
	err = c.kubeclientset.CoreV1().Secrets(cluster.Namespace).Delete(secretName, metav1.NewDeleteOptions(0))

	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to delete secret: %s", cluster.Namespace, cluster.Name, err.Error()))
		return false, err
	}

	return true, nil
}

func (c *Controller) createSecret(cluster *clusterv1alpha1.GKECluster, gkecluster *RemoteCluster) error {
	secretName := cluster.ObjectMeta.Labels["service.infrabox.net/secret-name"]
	secret, err := c.secretsLister.Secrets(cluster.Namespace).Get(secretName)

	if err != nil {
		if !errors.IsNotFound(err) {
			return err
		}
	}

	if secret != nil {
		return nil
	}

	// Secret does not yet exist
	glog.Infof("%s/%s: Creating secret for cluster credentials", cluster.Namespace, cluster.Name)
	secret, err = c.kubeclientset.CoreV1().Secrets(cluster.Namespace).Create(newSecret(cluster, gkecluster))

	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to create secret: %s", cluster.Namespace, cluster.Name, err.Error()))
		return err
	}

	return nil
}

func (c *Controller) deleteGKECluster(cluster *clusterv1alpha1.GKECluster) (bool, error) {
	// Get the GKE Cluster
	gkecluster, err := c.getRemoteCluster(cluster.Name)
	if err != nil {
		if errors.IsNotFound(err) {
			runtime.HandleError(fmt.Errorf("%s/%s: Could not get GKE Cluster", cluster.Namespace, cluster.Name))
			return false, err
		}
	}

	if gkecluster == nil {
		return true, nil
	}

	// Cluster still exists, delete it
	glog.Infof("%s/%s: deleting gke cluster", cluster.Namespace, cluster.Name)
	cmd := exec.Command("gcloud", "-q", "container", "clusters", "delete", "ib-"+cluster.Name, "--async", "--zone", "us-east1-b")
	out, err := cmd.CombinedOutput()

	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to delete cluster", cluster.Namespace, cluster.Name))
		glog.Warning(string(out))
		return false, err
	}

	return false, nil
}

func (c *Controller) deleteCluster(cluster *clusterv1alpha1.GKECluster) error {
	// Update status to pending
	if cluster.Status.Status != "pending" {
		cluster.Status.Status = "pending"
		cluster, err := c.gkeclientset.GcpV1alpha1().GKEClusters(cluster.Namespace).Update(cluster)

		if err != nil {
			runtime.HandleError(fmt.Errorf("%s/%s: Failed to update status", cluster.Namespace, cluster.Name))
			return err
		}
	}

	// Delete GKE Cluster
	deleted, err := c.deleteGKECluster(cluster)
	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to delete GKE cluster", cluster.Namespace, cluster.Name))
		return err
	}

	if !deleted {
		return nil
	}

	// Delete Secret
	deleted, err = c.deleteSecret(cluster)
	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to delete secret", cluster.Namespace, cluster.Name))
		return err
	}

	if !deleted {
		return nil
	}

	// Everything deleted, remove finalizers
	glog.Infof("%s/%s: removing finalizers", cluster.Namespace, cluster.Name)
	cluster.SetFinalizers([]string{})
	_, err = c.gkeclientset.GcpV1alpha1().GKEClusters(cluster.Namespace).Update(cluster)

	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to set finalizers", cluster.Namespace, cluster.Name))
		return err
	}

	/*
		glog.Infof("%s/%s: Finally deleting cluster", cluster.Namespace, cluster.Name)
		err = c.gkeclientset.GcpV1alpha1().GKEClusters(cluster.Namespace).Delete(cluster.Name, metav1.NewDeleteOptions(0))
		if err != nil {
			runtime.HandleError(fmt.Errorf("%s/%s: Failed to delete cluster", cluster.Namespace, cluster.Name))
			return err
		}
	*/

	return nil
}

func (c *Controller) syncHandler(key string) error {
	namespace, name, err := cache.SplitMetaNamespaceKey(key)
	if err != nil {
		runtime.HandleError(fmt.Errorf("invalid resource key: %s", key))
		return nil
	}

	cluster, err := c.clusterLister.GKEClusters(namespace).Get(name)

	if err != nil {
		if errors.IsNotFound(err) {
			runtime.HandleError(fmt.Errorf("%s: Cluster in work queue no longer exists", key))
			return nil
		}
		return err
	}

	err = c.syncHandlerImpl(key, cluster.DeepCopy())

	if err != nil {
		cluster = cluster.DeepCopy()
		cluster.Status.Status = "error"
		cluster.Status.Message = err.Error()
		_, err := c.gkeclientset.GcpV1alpha1().GKEClusters(cluster.Namespace).Update(cluster)

		if err != nil {
			runtime.HandleError(fmt.Errorf("%s: Failed to update status", key))
			return err
		}
	}

	return nil
}

func (c *Controller) syncHandlerImpl(key string, cluster *clusterv1alpha1.GKECluster) error {
	glog.Infof("%s: Start sync", key)

	// Check wether we should delete the cluster
	delTimestamp := cluster.GetDeletionTimestamp()
	if delTimestamp != nil {
		return c.deleteCluster(cluster)
	}

	if cluster.Status.Status == "error" {
		glog.Infof("%s: Cluster in error state, skipping", key)
		return nil
	}

	// Get the GKE Cluster
	gkecluster, err := c.getRemoteCluster(cluster.Name)
	if err != nil {
		if !errors.IsNotFound(err) {
			runtime.HandleError(fmt.Errorf("%s: Could not get GKE Cluster", key))
			return err
		}
	}

	if gkecluster == nil {
		glog.Infof("%s: Cluster does not exist yet, creating one", key)

		// First set finalizers so we don't forget to delete it later on
		cluster.SetFinalizers([]string{"gcp.service.infrabox.net"})
		cluster, err := c.gkeclientset.GcpV1alpha1().GKEClusters(cluster.Namespace).Update(cluster)

		if err != nil {
			runtime.HandleError(fmt.Errorf("%s: Failed to set finalizers", key))
			return err
		}

		name := "ib-" + cluster.Name
		args := []string{"container", "clusters",
			"create", name, "--async", "--zone", "us-east1-b", "--enable-autorepair"}

		if cluster.Spec.DiskSize != "" {
			args = append(args, "--disk-size")
			args = append(args, cluster.Spec.DiskSize)
		}

		if cluster.Spec.MachineType != "" {
			args = append(args, "--machine-type")
			args = append(args, cluster.Spec.MachineType)
		}

		if cluster.Spec.EnableNetworkPolicy == "true" {
			args = append(args, "--enable-network-policy")
		}

		if cluster.Spec.NumNodes != "" {
			args = append(args, "--num-nodes")
			args = append(args, cluster.Spec.NumNodes)
		}

		if cluster.Spec.Preemptible == "true" {
			args = append(args, "--preemptible")
		}

		if cluster.Spec.EnableAutoscaling == "true" {
			args = append(args, "--enable-autoscaling")

			if cluster.Spec.MaxNodes != "" {
				args = append(args, "--max-nodes")
				args = append(args, cluster.Spec.MaxNodes)
			}

			if cluster.Spec.MinNodes != "" {
				args = append(args, "--min-nodes")
				args = append(args, cluster.Spec.MinNodes)
			}
		}

		cmd := exec.Command("gcloud", args...)
		out, err := cmd.CombinedOutput()

		if err != nil {
			runtime.HandleError(fmt.Errorf("%s: Failed to create gke cluster", key))
			glog.Error(string(out))
			return err
		}

		glog.Infof("%s: Cluster creation started", key)
		gkecluster, err := c.getRemoteCluster(cluster.Name)

		if err != nil {
			runtime.HandleError(fmt.Errorf("%s: Could not get GKE Cluster", key))
			return err
		}

		err = c.updateClusterStatus(cluster, gkecluster)

		if err != nil {
			runtime.HandleError(fmt.Errorf("%s: Failed to update status", key))
			return err
		}
	} else {
		if err != nil {
			runtime.HandleError(fmt.Errorf("%s: Failed to create secret: %s", key, err.Error()))
			return err
		}

		if gkecluster.Status == "RUNNING" {
			glog.Infof("%s: Cluster is ready", key)
			err = c.createSecret(cluster, gkecluster)
		}

		err = c.updateClusterStatus(cluster, gkecluster)

		if err != nil {
			runtime.HandleError(fmt.Errorf("%s: Failed to update status", key))
			return err
		}
	}

	glog.Infof("%s: Finished sync", key)
	return nil
}

func (c *Controller) enqueueCluster(obj interface{}) {
	var key string
	var err error
	if key, err = cache.MetaNamespaceKeyFunc(obj); err != nil {
		runtime.HandleError(err)
		return
	}
	c.workqueue.AddRateLimited(key)
}
