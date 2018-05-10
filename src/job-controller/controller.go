package main

import (
	"crypto/rsa"
	"encoding/json"
	"fmt"
	jwt "github.com/dgrijalva/jwt-go"
	"io/ioutil"
	"os"
	"strconv"
	"time"

	goerr "errors"

	"github.com/golang/glog"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/runtime"
	"k8s.io/apimachinery/pkg/util/wait"
	"k8s.io/client-go/discovery"
	"k8s.io/client-go/dynamic"
	kubeinformers "k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/kubernetes/scheme"
	typedcorev1 "k8s.io/client-go/kubernetes/typed/core/v1"
	batchlisters "k8s.io/client-go/listers/batch/v1"
	"k8s.io/client-go/tools/cache"
	"k8s.io/client-go/tools/record"
	"k8s.io/client-go/util/workqueue"

	restclient "k8s.io/client-go/rest"

	jobv1alpha1 "github.com/infrabox/infrabox/src/job-controller/pkg/apis/jobcontroller/v1alpha1"
	clientset "github.com/infrabox/infrabox/src/job-controller/pkg/client/clientset/versioned"
	jobscheme "github.com/infrabox/infrabox/src/job-controller/pkg/client/clientset/versioned/scheme"
	informers "github.com/infrabox/infrabox/src/job-controller/pkg/client/informers/externalversions"
	listers "github.com/infrabox/infrabox/src/job-controller/pkg/client/listers/jobcontroller/v1alpha1"
)

const controllerAgentName = "infrabox-job-controller"

type Controller struct {
	kubeclientset                kubernetes.Interface
	jobclientset                 clientset.Interface
	jobLister                    listers.JobLister
	jobSynced                    cache.InformerSynced
	k8sJobLister                 batchlisters.JobLister
	k8sJobSynced                 cache.InformerSynced
	workqueue                    workqueue.RateLimitingInterface
	recorder                     record.EventRecorder
	config                       *restclient.Config
	generalDontCheckCertificates string
	localCacheEnabled            string
	jobMaxOutputSize             string
	jobMountdockerSocket         string
	daemonJSON                   string
	rootURL                      string
	tag                          string
	dockerRegistry               string
	localCacheHostPath           string
	gerritEnabled                string
	gerritUsername               string
	gerritHostname               string
	gerritPort                   string
}

// NewController returns a new job controller
func NewController(
	kubeclientset kubernetes.Interface,
	jobclientset clientset.Interface,
	kubeInformerFactory kubeinformers.SharedInformerFactory,
	jobInformerFactory informers.SharedInformerFactory,
	config *restclient.Config) *Controller {

	jobInformer := jobInformerFactory.Infrabox().V1alpha1().Jobs()
	k8sJobInformer := kubeInformerFactory.Batch().V1().Jobs()

	// Create event broadcaster
	// Add sample-controller types to the default Kubernetes Scheme so Events can be
	// logged for sample-controller types.
	jobscheme.AddToScheme(scheme.Scheme)
	glog.V(4).Info("Creating event broadcaster")
	eventBroadcaster := record.NewBroadcaster()
	eventBroadcaster.StartLogging(glog.Infof)
	eventBroadcaster.StartRecordingToSink(&typedcorev1.EventSinkImpl{Interface: kubeclientset.CoreV1().Events("")})
	recorder := eventBroadcaster.NewRecorder(scheme.Scheme, corev1.EventSource{Component: controllerAgentName})

	data, err := ioutil.ReadFile("/etc/docker/daemon.json")
	if err != nil {
		panic(err)
	}

	controller := &Controller{
		kubeclientset: kubeclientset,
		jobclientset:  jobclientset,
		jobLister:     jobInformer.Lister(),
		jobSynced:     jobInformer.Informer().HasSynced,
		k8sJobLister:  k8sJobInformer.Lister(),
		k8sJobSynced:  k8sJobInformer.Informer().HasSynced,
		workqueue:     workqueue.NewNamedRateLimitingQueue(workqueue.DefaultControllerRateLimiter(), "Clusters"),
		recorder:      recorder,
		config:        config,
		generalDontCheckCertificates: os.Getenv("INFRABOX_GENERAL_DONT_CHECK_CERTIFICATES"),
		localCacheEnabled:            os.Getenv("INFRABOX_LOCAL_CACHE_ENABLED"),
		jobMaxOutputSize:             os.Getenv("INFRABOX_JOB_MAX_OUTPUT_SIZE"),
		jobMountdockerSocket:         os.Getenv("INFRABOX_JOB_MOUNT_DOCKER_SOCKET"),
		daemonJSON:                   string(data),
		rootURL:                      os.Getenv("INFRABOX_ROOT_URL"),
		tag:                          os.Getenv("INFRABOX_VERSION"),
		dockerRegistry:               os.Getenv("INFRABOX_GENERAL_DOCKER_REGISTRY"),
		localCacheHostPath:           os.Getenv("INFRABOX_LOCAL_CACHE_HOST_PATH"),
		gerritEnabled:                os.Getenv("INFRABOX_GERRIT_ENABLED"),
	}

	if controller.gerritEnabled == "true" {
		controller.gerritHostname = os.Getenv("INFRABOX_GERRIT_HOSTNAME")
		controller.gerritUsername = os.Getenv("INFRABOX_GERRIT_USERNAME")
		controller.gerritPort = os.Getenv("INFRABOX_GERRIT_PORT")
	}

	glog.Info("Setting up event handlers")

	jobInformer.Informer().AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc: controller.enqueueJob,
		UpdateFunc: func(old, new interface{}) {
			controller.enqueueJob(new)
		},
		DeleteFunc: func(old interface{}) {
			glog.Info("Waiting for informer caches to sync")
		},
	})

	return controller
}

func (c *Controller) Run(threadiness int, stopCh <-chan struct{}) error {
	defer runtime.HandleCrash()
	defer c.workqueue.ShutDown()

	glog.Info("Starting Cluster controller")

	glog.Info("Waiting for informer caches to sync")
	if ok := cache.WaitForCacheSync(stopCh, c.jobSynced); !ok {
		return fmt.Errorf("failed to wait for caches to sync")
	}

	if ok := cache.WaitForCacheSync(stopCh, c.k8sJobSynced); !ok {
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

func (c *Controller) syncHandler(key string) error {
	namespace, name, err := cache.SplitMetaNamespaceKey(key)
	if err != nil {
		runtime.HandleError(fmt.Errorf("invalid resource key: %s", key))
		return nil
	}

	job, err := c.jobLister.Jobs(namespace).Get(name)

	if err != nil {
		if errors.IsNotFound(err) {
			runtime.HandleError(fmt.Errorf("%s: Cluster in work queue no longer exists", key))
			return nil
		}
		return err
	}

	if job.Status.Status == "error" {
		return nil
	}

	err = c.syncHandlerImpl(*job.DeepCopy())

	if err != nil {
		job = job.DeepCopy()
		job.Status.Status = "error"
		job.Status.Message = err.Error()
		_, err := c.jobclientset.InfraboxV1alpha1().Jobs(job.Namespace).Update(job)

		if err != nil {
			runtime.HandleError(fmt.Errorf("%s: Failed to update status", key))
			return err
		}
	}

	return nil
}

func (c *Controller) newBatchJob(job *jobv1alpha1.Job, token string) *batchv1.Job {
	volumes := []corev1.Volume{
		corev1.Volume{
			Name: "data-dir",
		},
		corev1.Volume{
			Name: "repo",
		},
	}

	volumeMounts := []corev1.VolumeMount{
		corev1.VolumeMount{
			MountPath: "/data",
			Name:      "data-dir",
		},
		corev1.VolumeMount{
			MountPath: "/repo",
			Name:      "repo",
		},
	}

	mem, _ := job.Spec.Resources.Limits.Memory().AsInt64()
	mem = mem / 1024 / 1024

	env := []corev1.EnvVar{
		corev1.EnvVar{
			Name:  "INFRABOX_JOB_ID",
			Value: job.Name,
		},
		corev1.EnvVar{
			Name:  "INFRABOX_GENERAL_DONT_CHECK_CERTIFICATES",
			Value: c.generalDontCheckCertificates,
		},
		corev1.EnvVar{
			Name:  "INFRABOX_JOB_API_URL",
			Value: c.rootURL + "/api/job",
		},
		corev1.EnvVar{
			Name:  "INFRABOX_JOB_GIT_URL",
			Value: "http://localhost:8080",
		},
		corev1.EnvVar{
			Name:  "INFRABOX_SERVICE",
			Value: "job",
		},
		corev1.EnvVar{
			Name:  "INFRABOX_VERSION",
			Value: c.tag,
		},
		corev1.EnvVar{
			Name:  "INFRABOX_LOCAL_CACHE_ENABLED",
			Value: c.localCacheEnabled,
		},
		corev1.EnvVar{
			Name:  "INFRABOX_JOB_MAX_OUTPUT_SIZE",
			Value: c.jobMaxOutputSize,
		},
		corev1.EnvVar{
			Name:  "INFRABOX_JOB_MOUNT_DOCKER_SOCKET",
			Value: c.jobMountdockerSocket,
		},
		corev1.EnvVar{
			Name:  "INFRABOX_JOB_DAEMON_JSON",
			Value: c.daemonJSON,
		},
		corev1.EnvVar{
			Name:  "INFRABOX_ROOT_URL",
			Value: c.rootURL,
		},
		corev1.EnvVar{
			Name:  "INFRABOX_JOB_TOKEN",
			Value: token,
		},
		corev1.EnvVar{
			Name:  "INFRABOX_JOB_RESOURCES_LIMITS_MEMORY",
			Value: strconv.FormatInt(mem, 10),
		},
		corev1.EnvVar{
			Name:  "INFRABOX_JOB_RESOURCES_LIMITS_CPU",
			Value: job.Spec.Resources.Limits.Cpu().String(),
		},
	}

	env = append(env, job.Spec.Env...)

	if c.localCacheEnabled == "true" {
		volumes = append(volumes, corev1.Volume{
			Name: "local-cache",
			VolumeSource: corev1.VolumeSource{
				HostPath: &corev1.HostPathVolumeSource{
					Path: c.localCacheHostPath,
				},
			},
		})

		volumeMounts = append(volumeMounts, corev1.VolumeMount{
			MountPath: "/local-cache",
			Name:      "local-cache",
		})
	}

	cloneEnv := []corev1.EnvVar{
		corev1.EnvVar{
			Name:  "INFRABOX_GENERAL_DONT_CHECK_CERTIFICATES",
			Value: c.generalDontCheckCertificates,
		},
	}
	cloneVolumeMounts := []corev1.VolumeMount{
		corev1.VolumeMount{
			MountPath: "/repo",
			Name:      "repo",
		},
	}

	if c.gerritEnabled == "true" {
		gerritEnv := []corev1.EnvVar{
			corev1.EnvVar{
				Name:  "INFRABOX_GERRIT_HOSTNAME",
				Value: c.gerritHostname,
			},
			corev1.EnvVar{
				Name:  "INFRABOX_GERRIT_USERNAME",
				Value: c.gerritUsername,
			},
			corev1.EnvVar{
				Name:  "INFRABOX_GERRIT_PORT",
				Value: c.gerritPort,
			},
		}

		env = append(env, gerritEnv...)
		cloneEnv = append(env, gerritEnv...)

		cloneVolumeMounts = append(cloneVolumeMounts, corev1.VolumeMount{
			Name:      "gerrit-ssh",
			MountPath: "/tmp/gerrit/",
		})

		volumes = append(volumes, corev1.Volume{
			Name: "gerrit-ssh",
			VolumeSource: corev1.VolumeSource{
				Secret: &corev1.SecretVolumeSource{
					SecretName: "infrabox-gerrit-ssh",
				},
			},
		})
	}

	t := true
	f := false

	runJob := corev1.Container{
		Name:            "run-job",
		ImagePullPolicy: "Always",
		Image:           c.dockerRegistry + "/job:" + c.tag,
		SecurityContext: &corev1.SecurityContext{
			Privileged: &t,
		},
		Env: env,
		Resources: corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				"cpu":    job.Spec.Resources.Limits.Cpu().DeepCopy(),
				"memory": job.Spec.Resources.Limits.Memory().DeepCopy(),
			},
			Limits: corev1.ResourceList{
				"cpu": job.Spec.Resources.Limits.Cpu().DeepCopy(),
			},
		},
		VolumeMounts: volumeMounts,
	}

	gitJob := corev1.Container{
		Name:            "git-clone",
		ImagePullPolicy: "Always",
		Image:           c.dockerRegistry + "/job-git:" + c.tag,
		Env:             cloneEnv,
		VolumeMounts:    cloneVolumeMounts,
	}

	containers := []corev1.Container{
		gitJob, runJob,
	}

	return &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name:      job.Name,
			Namespace: job.Namespace,
			OwnerReferences: []metav1.OwnerReference{
				*metav1.NewControllerRef(job, schema.GroupVersionKind{
					Group:   jobv1alpha1.SchemeGroupVersion.Group,
					Version: jobv1alpha1.SchemeGroupVersion.Version,
					Kind:    "Job",
				}),
			},
		},
		Spec: batchv1.JobSpec{
			Template: corev1.PodTemplateSpec{
				Spec: corev1.PodSpec{
					AutomountServiceAccountToken: &f,
					Containers:                   containers,
					Volumes:                      volumes,
					RestartPolicy:                "OnFailure",
				},
			},
		},
	}
}

func (c *Controller) deleteBatchJob(job *jobv1alpha1.Job) (bool, error) {
	batch, err := c.jobLister.Jobs(job.Namespace).Get(job.Name)

	if err != nil {
		return errors.IsNotFound(err), err
	}

	if batch == nil {
		return true, nil
	}

	glog.Infof("%s/%s: Deleting Batch Job", job.Namespace, job.Name)
	err = c.kubeclientset.BatchV1().Jobs(job.Namespace).Delete(job.Name, &metav1.DeleteOptions{})

	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to delete job: %s", job.Namespace, job.Name, err.Error()))
		return false, err
	}

	return true, nil
}

func (c *Controller) deleteJob(job *jobv1alpha1.Job) error {
	servicesDeleted, err := c.deleteServices(job)
	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to delete service: %s", job.Namespace, job.Name, err.Error()))
		return err
	}

	batchDeleted, err := c.deleteBatchJob(job)
	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to delete batch job: %s", job.Namespace, job.Name, err.Error()))
		return err
	}

	if !servicesDeleted {
		glog.Infof("%s/%s: Not all services deleted yet", job.Namespace, job.Name)
		return nil
	}

	if !batchDeleted {
		glog.Infof("%s/%s: Batch not deleted yet", job.Namespace, job.Name)
		return nil
	}

	// Everything deleted, remove finalizers and delete job
	glog.Infof("%s/%s: removing finalizers", job.Namespace, job.Name)
	job.SetFinalizers([]string{})
	_, err = c.jobclientset.InfraboxV1alpha1().Jobs(job.Namespace).Update(job)

	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to set finalizers", job.Namespace, job.Name))
		return err
	}

	glog.Infof("%s/%s: Successfully deleted job", job.Namespace, job.Name)
	return nil
}

func (c *Controller) deleteService(job *jobv1alpha1.Job, service *jobv1alpha1.Service) (bool, error) {
	glog.Infof("%s/%s: Deleting Service", job.Namespace, job.Name)

	si, err := c.getServiceInterface(service, job)
	if err != nil {
		return false, err
	}

	err = si.Delete(service.Name, &metav1.DeleteOptions{})

	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to delete service: %s", job.Namespace, job.Name, err.Error()))
		return false, err
	}

	glog.Infof("%s/%s: Successfully deleted service", job.Namespace, job.Name)
	return true, nil
}

func (c *Controller) getServiceInterface(service *jobv1alpha1.Service, job *jobv1alpha1.Job) (dynamic.ResourceInterface, error) {
	client, err := discovery.NewDiscoveryClientForConfig(c.config)
	if err != nil {
		return nil, err
	}

	resourceList, err := client.ServerResourcesForGroupVersion(service.TypeMeta.APIVersion)

	if err != nil {
		return nil, err
	}

	var resource metav1.APIResource
	for _, res := range resourceList.APIResources {
		if res.Kind == service.Kind {
			resource = res
			break
		}
	}

	dyn, err := dynamic.NewClient(c.config)
	if err != nil {
		return nil, err
	}

	r := dyn.Resource(&resource, job.Namespace)
	return r, err
}

func (c *Controller) createService(service *jobv1alpha1.Service, job *jobv1alpha1.Job) (bool, error) {
	si, err := c.getServiceInterface(service, job)

	if err != nil {
		return false, err
	}

	id, ok := service.ObjectMeta.Labels["service.infrabox.net/id"]
	if !ok {
		return false, goerr.New("Infrabox service id not set")
	}

	s, err := si.Get(id, metav1.GetOptions{})

	if err != nil {
		if !errors.IsNotFound(err) {
			return false, err
		}
	}

	if s != nil {
		// Already exists, check status
		var remote jobv1alpha1.Service
		serviceJson, err := s.MarshalJSON()
		if err != nil {
			runtime.HandleError(fmt.Errorf("Failed to parse service"))
			return false, err
		}

		err = json.Unmarshal(serviceJson, &remote)

		if err != nil {
			runtime.HandleError(fmt.Errorf("Failed to parse service"))
			return false, err
		}

		if remote.Status.Status == "ready" {
			return true, nil
		}

		if remote.Status.Status == "error" {
			return false, goerr.New("Failed to create service: " + remote.Status.Message)
		}
	} else {
		newService := service.DeepCopy()
		newService.ObjectMeta.Labels["service.infrabox.net/secret-name"] = id
		newService.ObjectMeta.Name = id
		newService.ObjectMeta.Namespace = job.Namespace

		bytes, err := json.Marshal(newService)
		if err != nil {
			return false, err
		}

		var tmp map[string]interface{}
		err = json.Unmarshal(bytes, &tmp)
		if err != nil {
			return false, err
		}

		si.Create(&unstructured.Unstructured{Object: tmp})
	}

	return true, nil
}

func (c *Controller) deleteServices(job *jobv1alpha1.Job) (bool, error) {
	if job.Spec.Services == nil {
		return true, nil
	}

	glog.Infof("%s/%s: Delete additional services", job.Namespace, job.Name)

	ready := true
	for _, s := range job.Spec.Services {
		r, err := c.deleteService(job, &s)

		if err != nil {
			return false, nil
		}

		if r {
			glog.Infof("%s/%s: Service %s/%s deleted", job.Namespace, job.Name, s.TypeMeta.APIVersion, s.Kind)
		} else {
			ready = false
			glog.Infof("%s/%s: Service %s/%s not yet deleted", job.Namespace, job.Name, s.TypeMeta.APIVersion, s.Kind)
		}
	}

	return ready, nil
}

func (c *Controller) createServices(job *jobv1alpha1.Job) (bool, error) {
	if job.Spec.Services == nil {
		return true, nil
	}

	glog.Infof("%s/%s: Create additional services", job.Namespace, job.Name)

	ready := true
	for _, s := range job.Spec.Services {
		r, err := c.createService(&s, job)

		if err != nil {
			return false, nil
		}

		if r {
			glog.Infof("%s/%s: Service %s/%s ready", job.Namespace, job.Name, s.TypeMeta.APIVersion, s.Kind)
		} else {
			ready = false
			glog.Infof("%s/%s: Service %s/%s not yet ready", job.Namespace, job.Name, s.TypeMeta.APIVersion, s.Kind)
		}
	}

	return ready, nil
}

func (c *Controller) createBatchJob(job *jobv1alpha1.Job) error {
	glog.Infof("%s/%s: Creating Batch Job", job.Namespace, job.Name)

	keyPath := os.Getenv("INFRABOX_RSA_PRIVATE_KEY_PATH")

	if keyPath == "" {
		keyPath = "/var/run/secrets/infrabox.net/rsa/id_rsa"
	}

	var signKey *rsa.PrivateKey

	signBytes, err := ioutil.ReadFile(keyPath)
	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to creat token", job.Namespace, job.Name))
		return err
	}

	signKey, err = jwt.ParseRSAPrivateKeyFromPEM(signBytes)
	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to creat token", job.Namespace, job.Name))
		return err
	}

	t := jwt.NewWithClaims(jwt.GetSigningMethod("RS256"), jwt.MapClaims{
		"job": map[string]string{
			"id": job.Name,
		},
		"type": "job",
	})

	token, err := t.SignedString(signKey)

	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to creat token", job.Namespace, job.Name))
		return err
	}

	_, err = c.kubeclientset.BatchV1().Jobs(job.Namespace).Create(c.newBatchJob(job, token))

	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to create job: %s", job.Namespace, job.Name, err.Error()))
		return err
	}

	glog.Infof("%s/%s: Successfully created job", job.Namespace, job.Name)
	return nil
}

func (c *Controller) createJob(job *jobv1alpha1.Job) error {
	// First set finalizers so we don't forget to delete it later on
	job.SetFinalizers([]string{"job.infrabox.net"})
	job, err := c.jobclientset.InfraboxV1alpha1().Jobs(job.Namespace).Update(job)

	if err != nil {
		runtime.HandleError(fmt.Errorf("%s/%s: Failed to set finalizers", job.Namespace, job.Name))
		return err
	}

	servicesCreated, err := c.createServices(job)

	if err != nil {
		return err
	}

	if !servicesCreated {
		return nil
	}

	err = c.createBatchJob(job)

	if err != nil {
		return err
	}

	return nil
}

func (c *Controller) syncHandlerImpl(job jobv1alpha1.Job) error {
	// Check wether we should delete the job
	delTimestamp := job.GetDeletionTimestamp()
	if delTimestamp != nil {
		return c.deleteJob(&job)
	}

	// Get the K8s Job
	k8sjob, err := c.k8sJobLister.Jobs(job.Namespace).Get(job.Name)

	if err != nil {
		if !errors.IsNotFound(err) {
			return err
		}
	}

	if k8sjob == nil {
		return c.createJob(&job)
	}

	return nil
}

func (c *Controller) enqueueJob(obj interface{}) {
	var key string
	var err error
	if key, err = cache.MetaNamespaceKeyFunc(obj); err != nil {
		runtime.HandleError(err)
		return
	}
	c.workqueue.AddRateLimited(key)
}